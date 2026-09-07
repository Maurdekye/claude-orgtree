"""Multiple real temporary repositories, actual UI, no live API or browser routing."""
import functools, http.server, json, os, sys, threading, time
from pathlib import Path
from urllib.parse import urlsplit

BROWSER_ENV = os.environ.copy()
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend/tests'))
# The existing fixture sets throwaway data/config BEFORE importing store.
from test_git_workspace import Fixture, git, gw, gitsettings, store, DATA
from orgtree import gitapi, appsettings
from orgtree.ledger import USER
from fastapi import FastAPI
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright
assert Path(store.DATA_ROOT).resolve() == DATA.resolve()
assert Path(gw.__file__).resolve().is_relative_to(ROOT)

def run():
    out = ROOT / 'frontend/node_modules/.orgtree-gitpanels'
    assert (out / 'gitpanels-fixture.js').is_file(), 'INERT: build the fixture first'
    a, b, sparse_repo = Fixture(), Fixture(), Fixture()
    sparse_id = gw.register(a.slug, str(sparse_repo.clone))['id']
    a.commit(a.clone, 'alpha.txt', 'alpha\n'); a.history(150)
    b.commit(b.clone, 'beta.txt', 'beta\n'); b.history(160)
    second = gw.register(a.slug, str(b.clone))['id']
    gitsettings.change(lambda d: d['selected_by_org'].update({a.slug: a.rid}))
    appsettings.set_git_periodic_fetch_enabled(True)
    a.org.hire(USER,None,'haiku',0,'owner')
    ticket=a.org.work_create(USER,'Panel navigation','Isolated navigation fixture',owner='owner')
    store.save_org(a.org)
    for rid in [a.rid,second]:
        for branch in ['main','long']:gw.link_item(a.slug,rid,'refs/heads/'+branch,ticket['slug'])
    app = FastAPI(); app.include_router(gitapi.router)
    client = TestClient(app)
    requests, errors, results, aborted = [], [], [], []
    held_snapshot = dict(rid=None, release=threading.Event(), entered=threading.Event(), fail=True)
    body = 'Typed message expands with the window. ' * 80
    event = dict(v=1, variant='ordinary.message', actor=dict(kind='user', id='user'), object=None, engine_authored=False, body=body)
    messages = [dict(role='user', text=body, seq=1, segments=[dict(kind='mail', rows=[dict(id='width-row', at='2026-09-07T00:00:00Z', **{'from':'user'}, kind='message', body=body, ev=event)])]),
                dict(role='assistant', text='Assistant width control. ' * 80, seq=2)]
    html = '<!doctype html><html><head><link rel="stylesheet" href="/gitpanels-fixture.css"></head><body><div id="root"></div><script>window.panelFixture=' + json.dumps(dict(slug=a.slug)) + '</script><script type="module" src="/gitpanels-fixture.js"></script></body></html>'
    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *_): pass
        def reply(self, value, code=200, content_type='application/json'):
            data = value if isinstance(value, bytes) else json.dumps(value).encode()
            self.send_response(code); self.send_header('Content-Type', content_type); self.send_header('Content-Length', str(len(data))); self.end_headers()
            try:self.wfile.write(data)
            except (ConnectionAbortedError,ConnectionResetError,BrokenPipeError):aborted.append(self.path)
        def api(self):
            path = urlsplit(self.path).path
            content = self.rfile.read(int(self.headers.get('content-length', '0')))
            entry=dict(method=self.command, path=self.path); requests.append(entry)
            if path.endswith('/chat'):
                self.reply(dict(messages=messages, total=2, busy=False, pending_mail=[], session_id='width-fixture', node='reader')); return
            if '/git' not in path:
                self.reply({}); return
            if path.endswith('/snapshot') and held_snapshot['rid'] and '/'+held_snapshot['rid']+'/' in path:
                held_snapshot['entered'].set()
                if not held_snapshot['release'].wait(15):
                    self.reply({'detail':'INERT held refresh was not released'},503);return
                if held_snapshot['fail']:
                    self.reply({'detail':'fixture refresh unavailable'},503);return
            response = client.request(self.command, self.path, content=content, headers={'content-type':'application/json'})
            if path.endswith(('/observation','/watch','/snapshot','/history')): entry['response']=response.json()
            self.reply(response.content, response.status_code)
        def do_GET(self):
            if self.path.startswith('/api/'): self.api()
            elif urlsplit(self.path).path == '/': self.reply(html.encode(), content_type='text/html')
            else: super().do_GET()
        do_POST = api
        do_PATCH = api
        do_DELETE = api
    server = http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=str(out)))
    thread = threading.Thread(target=server.serve_forever); thread.start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='msedge', env={**BROWSER_ENV, 'ORGTREE_DATA': str(DATA)})
            # First graph publication in a fresh child: no prior scroll can supply view size.
            # Hold the real initial response until the surface and owner viewport exist.
            pristine=[]
            gitsettings.change(lambda d:d['selected_by_org'].update({a.slug:sparse_id}))
            for child_width,child_height in [(2400,1400),(800,900)]:
                held_snapshot.update(rid=sparse_id,fail=False)
                held_snapshot['release'].clear();held_snapshot['entered'].clear()
                fresh=browser.new_context(viewport=dict(width=1800,height=1000))
                fresh_page=fresh.new_page();fresh_page.on('pageerror',lambda e:errors.append(str(e)))
                fresh_page.goto(f'http://127.0.0.1:{server.server_port}')
                fresh_page.get_by_role('button',name='Open Git repositories',exact=True).click()
                assert held_snapshot['entered'].wait(10),'INERT initial graph response hold'
                with fresh_page.expect_popup() as opened:
                    fresh_page.get_by_role('button',name='Open in new window',exact=True).click()
                pristine_child=opened.value
                pristine_child.set_viewport_size(dict(width=child_width,height=child_height))
                pristine_child.wait_for_function('[...document.querySelectorAll("link[rel=stylesheet]")].every(e=>e.sheet)')
                pristine_child.evaluate("""() => { window.initialGraphScrolls=[]; document.addEventListener("scroll",e=>{if(e.target.matches?.(".git-viewport"))initialGraphScrolls.push([e.target.scrollLeft,e.target.scrollTop])},true) }""")
                held_snapshot['release'].set()
                pristine_child.locator('.git-node').first.wait_for()
                pristine_child.evaluate('() => new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                dims=pristine_child.locator('.git-viewport').evaluate("""e=>{const c=e.querySelector(".git-canvas"),end=e.querySelector(".git-history-end");return {width:e.clientWidth,height:e.clientHeight,canvasWidth:c.getBoundingClientRect().width,canvasHeight:c.getBoundingClientRect().height,left:e.scrollLeft,top:e.scrollTop,trunk:parseFloat(end.style.left)+80,scrolls:window.initialGraphScrolls}}""")
                assert dims['height']>570 and dims['canvasHeight']==dims['height'],('pristine sparse child must fill before any pan',dims)
                expected=max(0,dims['trunk']-dims['width']/2)
                assert abs(dims['left']-expected)<2 and dims['top']==0,('pristine initial trunk centring',dims,expected)
                if child_width==2400:
                    assert dims['scrolls']==[] and expected==0,('INERT fresh wide graph was scrolled',dims)
                    assert dims['canvasWidth']==dims['width'],('pristine wide canvas',dims)
                else: assert expected>200,('INERT centring needs nonzero pan',dims)
                pristine.append(dims);fresh.close()
            held_snapshot.update(rid=None,fail=True)
            gitsettings.change(lambda d:d['selected_by_org'].update({a.slug:a.rid}))
            results.append(dict(pristine_initial_geometry=pristine))
            held_snapshot['release'].clear();held_snapshot['entered'].clear()
            if '--pristine-only' in sys.argv:
                assert not errors,errors
                browser.close();print(json.dumps(dict(passed=results,errors=errors,data=str(DATA)),indent=2));return
            context = browser.new_context(viewport=dict(width=1800,height=1000))
            context.set_default_timeout(15000)
            page = context.new_page(); page.on('pageerror',lambda e:errors.append(str(e)))
            page.add_init_script('Object.defineProperty(crypto,"randomUUID",{value:undefined});window.gitPending=0; const originalFetch=window.fetch; window.fetch=async(...args)=>{window.gitPending++;try{return await originalFetch(...args)}finally{window.gitPending--}}')
            page.clock.install(); page.clock.pause_at(time.time() * 1000 + 100)
            def counts():
                return {(rid,kind):sum(r['path'].endswith('/'+rid+'/'+kind) for r in requests) for rid in [a.rid,second] for kind in ['observation','watch']}
            def tick(milliseconds, expected):
                before=counts(); page.clock.run_for(milliseconds)
                # Virtual time advances production intervals; real responses still finish over local HTTP.
                deadline=time.monotonic()+10
                while time.monotonic()<deadline:
                    delta={key:value-before[key] for key,value in counts().items()}
                    if all(delta[key]>=value for key,value in expected.items()) and page.evaluate('gitPending===0'): break
                    page.wait_for_timeout(50)
                page.wait_for_timeout(250)
                delta={key:value-before[key] for key,value in counts().items()}
                assert delta==expected, ('mounted polling/watch mismatch',delta,expected,requests[-12:],page.evaluate('({pending:gitPending,now:performance.now()})'))
                gw.scheduler.stop()  # Drain real throwaway Git fetch jobs before the next virtual tick.
                return {rid+':'+kind:value for (rid,kind),value in delta.items()}
            def graph_ready(panel):
                page.wait_for_timeout(50)
                page.wait_for_function('gitPending===0 && !document.querySelector(".git-loading")',polling=50)
                page.clock.run_for(32)
                panel.locator('.git-node').first.wait_for()
            page.goto(f'http://127.0.0.1:{server.server_port}')
            page.get_by_role('button',name='Open Git repositories',exact=True).click()
            first = page.locator('.git-workspace').nth(0)
            graph_ready(first)
            first.locator('.git-ticket-row button').first.click()
            assert page.locator('.git-workspace').count()==0 and page.evaluate('lastPanelRef.ref.kind')=='item', 'centered reference navigation must close its panel'
            page.get_by_role('button',name='Open Git repositories',exact=True).click()
            graph_ready(first)
            first.get_by_role('button',name='Branches and history',exact=True).click()
            first.get_by_label('long',exact=True).uncheck()
            page.wait_for_function('document.querySelectorAll(".git-loading").length===0',polling=50)
            first.get_by_label('long',exact=True).check()
            page.wait_for_function('document.querySelectorAll(".git-loading").length===0',polling=50)
            first.get_by_role('button',name='Branches and history',exact=True).click()
            original_queries=sum(a.rid in r['path'] and 'branches=' in r['path'] for r in requests)
            page.evaluate('() => { window.originalPanel=document.querySelector(".git-viewport") }')
            first.get_by_role('button',name='Open another panel',exact=True).click()
            page.wait_for_function('document.querySelectorAll(".git-workspace").length===2')
            second_panel = page.locator('.git-workspace').nth(1)
            graph_ready(second_panel)
            ra, rb = first.bounding_box(),second_panel.bounding_box()
            assert ra['x']+ra['width'] <= rb['x']+1, (ra,rb)
            first.locator('.git-ticket-row button').first.click()
            assert page.locator('.git-workspace').count()==2, 'reference navigation closed a pinned panel'
            assert first.get_by_label('Repository',exact=True).input_value()==a.rid
            same_repo=tick(5000,{(a.rid,'observation'):1,(a.rid,'watch'):1,(second,'observation'):0,(second,'watch'):0})
            # Hold the actual repository lock for one observation: busy is not disabled.
            mutex=gw.lock(gw.repository(a.slug,a.rid)); mutex.acquire()
            try:
                busy_poll=tick(5000,{(a.rid,'observation'):1,(a.rid,'watch'):0,(second,'observation'):0,(second,'watch'):0})
                assert requests[-1]['response']=={'busy':True}, 'INERT busy observation control'
            finally: mutex.release()
            cadence=[tick(5000,{(a.rid,'observation'):1,(a.rid,'watch'):int(i==4),(second,'observation'):0,(second,'watch'):0}) for i in range(5)]
            second_panel.get_by_label('Repository',exact=True).select_option(second)
            graph_ready(second_panel)
            assert first.get_by_label('Repository',exact=True).input_value()==a.rid
            assert second_panel.get_by_label('Repository',exact=True).input_value()==second
            different_repo=tick(5000,{(a.rid,'observation'):1,(a.rid,'watch'):0,(second,'observation'):1,(second,'watch'):1})
            assert first.locator('.git-viewport').evaluate('e=>e.scrollHeight>e.clientHeight+500'), 'INERT scroll fixture'
            first.locator('.git-viewport').evaluate('e=>e.scrollTop=500')
            assert first.locator('.git-viewport').evaluate('e=>e.scrollTop')==500
            second_panel.get_by_role('button',name='Branches and history',exact=True).click()
            second_panel.get_by_label('long',exact=True).uncheck()
            page.wait_for_function('document.querySelectorAll(".git-loading").length===0',polling=50)
            second_panel.get_by_label('long',exact=True).check()
            page.wait_for_function('document.querySelectorAll(".git-loading").length===0',polling=50)
            assert first.locator('.git-viewport').evaluate('e=>e.scrollTop')==500
            assert any(second in r['path'] and 'branches=' in r['path'] for r in requests)
            assert sum(a.rid in r['path'] and 'branches=' in r['path'] for r in requests)==original_queries
            second_panel.get_by_role('button',name='Branches and history',exact=True).click()
            graph_ready(second_panel)
            page.screenshot(path=str(out/'side-by-side.png'))
            results.append('two real repositories side by side; repository and branch selections do not change sibling scroll or queries')
            page.evaluate('() => { window.secondViewport=document.querySelectorAll(".git-viewport")[1] }')
            for width,height in [(600,410),(820,720)]:
                page.evaluate('([w,h])=>panelProbe.resize(panelProbe.panels()[1].kind,{x:950,y:70,w,h})',[width,height])
                page.wait_for_timeout(200)
                bounds = second_panel.bounding_box(); vp=second_panel.locator('.git-viewport').bounding_box(); footer=second_panel.locator('.git-footer').bounding_box()
                assert vp['height']>35 and vp['y']+vp['height']<=bounds['y']+bounds['height'],(bounds,vp)
                assert footer['y']+footer['height']<=bounds['y']+bounds['height']+1,(bounds,footer)
            results.append('short and tall pinned panels keep graph and footer inside panel')
            # Branch selection has refreshed B; settle that new subscription before detaching.
            before_detach=tick(5000,{(a.rid,'observation'):1,(a.rid,'watch'):0,(second,'observation'):1,(second,'watch'):1})
            with page.expect_popup() as popped: second_panel.get_by_role('button',name='Open in new window',exact=True).click()
            child=popped.value;child.on('pageerror',lambda e:errors.append(str(e)))
            child.locator('.git-ticket-row button').first.click()
            assert not child.is_closed() and child.locator('.git-workspace').count()==1, 'reference navigation closed a detached panel'
            detached_poll=tick(5000,{(a.rid,'observation'):1,(a.rid,'watch'):0,(second,'observation'):1,(second,'watch'):0})
            child.locator('.git-viewport').evaluate('e=>e.scrollTop=300')
            widths=[]
            for width,height in [(1900,1100),(2400,1400)]:
                child.set_viewport_size(dict(width=width,height=height));child.wait_for_timeout(600)
                panel=child.locator('.git-workspace').bounding_box(); vp=child.locator('.git-viewport').bounding_box(); canvas=child.locator('.git-canvas').bounding_box()
                assert abs(panel['width']-width)<2 and abs(vp['width']-width)<2,(width,panel,vp)
                assert canvas['width']>=vp['width']-16 and canvas['height']>=vp['height']-16,(vp,canvas)
                assert child.locator('.git-viewport').evaluate('e=>e.scrollTop')==300, 'resize reset vertical pan'
                child.screenshot(path=str(out/f'detached-{width}.png'))
                widths.append(dict(window=[width,height],panel=panel,viewport=vp,canvas=canvas))
            child.locator('.git-viewport').evaluate('e=>e.scrollTop=300')
            vp=child.locator('.git-viewport').bounding_box()
            child.mouse.move(vp['x']+50,vp['y']+150);child.mouse.down();child.mouse.move(vp['x']+50,vp['y']+90,steps=6);child.mouse.up()
            assert child.locator('.git-viewport').evaluate('e=>e.scrollTop')>300
            assert page.evaluate('secondViewport.isConnected && secondViewport.ownerDocument !== document')
            child.get_by_role('button',name='Branches and history',exact=True).click()
            child.get_by_role('button',name='Relevant branches',exact=True).click()
            child.get_by_role('button',name='Branches and history',exact=True).click()
            child.get_by_label('long',exact=True).uncheck()
            child.get_by_role('button',name='Branches and history',exact=True).click()
            page.wait_for_timeout(300)
            child.wait_for_function('document.querySelectorAll(".git-loading").length===0',polling=50)
            sparse=child.locator('.git-canvas').bounding_box()
            vph=child.locator('.git-viewport').evaluate('e=>e.clientHeight')
            assert abs(sparse['height']-vph)<2 and vph>570, ('sparse history must fill tall child',sparse,vph)
            graph_ready(child.locator('.git-workspace'))
            child.set_viewport_size(dict(width=2600,height=1550));child.wait_for_timeout(300)
            refreshed_canvas=child.locator('.git-canvas').bounding_box();refreshed_view=child.locator('.git-viewport').bounding_box()
            assert abs(refreshed_canvas['width']-refreshed_view['width'])<2 and abs(refreshed_canvas['height']-refreshed_view['height'])<2, ('refresh lost its viewport observer',refreshed_canvas,refreshed_view)
            child.set_viewport_size(dict(width=2400,height=1400));child.wait_for_timeout(300)
            child.locator('.git-ticket-row button').first.click()
            assert child.locator('.git-workspace').count()==1, 'post-refresh navigation must remain rendered and usable'
            child.evaluate('() => { opener.secondViewport=document.querySelector(".git-viewport") }')
            # Persisted default differs from the source: a new panel must use its explicit seed.
            gitsettings.change(lambda d:d['selected_by_org'].update({a.slug:a.rid}))
            child.get_by_role('button',name='Open another panel',exact=True).click()
            assert page.locator('.git-workspace').count()==2 and child.locator('.git-workspace').count()==1
            extra=page.locator('.git-workspace').nth(1)
            graph_ready(extra)
            assert extra.get_by_label('Repository',exact=True).input_value()==second
            extra_id=page.evaluate('panelProbe.panels().at(-1).kind')
            assert page.evaluate('(id)=>!!panelProbe.pins()[id]',extra_id), 'INERT extra pin cleanup control'
            extra.get_by_role('button',name='close this window',exact=True).click()
            assert page.evaluate('(id)=>!panelProbe.pins()[id]',extra_id), 'closed extra pin geometry leaked'
            assert page.locator('.git-workspace').count()==1 and child.locator('.git-workspace').count()==1, 'closing extra panel removed siblings'
            child.close();second_panel.locator('.git-viewport').wait_for()
            assert page.evaluate('document.querySelectorAll(".git-viewport")[1]===secondViewport')
            returned_poll=tick(5000,{(a.rid,'observation'):1,(a.rid,'watch'):0,(second,'observation'):1,(second,'watch'):1})
            page.evaluate('window.leakClosedGitObserver=true')  # Activated only by the explicit cleanup mutant.
            second_panel.get_by_role('button',name='close this window',exact=True).click()
            assert page.locator('.git-workspace').count()==1
            assert page.evaluate('document.querySelector(".git-viewport")===originalPanel')
            assert first.get_by_label('Repository',exact=True).input_value()==a.rid
            assert first.locator('.git-viewport').evaluate('e=>e.scrollTop')==500
            last_b_close=tick(5000,{(a.rid,'observation'):1,(a.rid,'watch'):0,(second,'observation'):0,(second,'watch'):0})
            first.get_by_role('button',name='close this window',exact=True).click()
            last_close=tick(35000,{(a.rid,'observation'):0,(a.rid,'watch'):0,(second,'observation'):0,(second,'watch'):0})
            results.append('same-repo mounted views share observation/watch; different repos poll independently; detached/returned view stays subscribed; last close stops requests')
            results.append('detached panel fills both wide child sizes, graph drags, native return preserves DOM; closing it leaves sibling intact')
            page.get_by_role('button',name='Open Git repositories',exact=True).click()
            graph_ready(first)
            first.get_by_role('button',name='Open another panel',exact=True).click()
            extra_id=page.evaluate('panelProbe.panels().at(-1).kind')
            assert page.evaluate('(id)=>!!panelProbe.pins()[id]',extra_id)
            page.evaluate('panelProbe.org("another-org")')
            assert page.locator('.git-workspace').count()==0
            assert page.evaluate('(id)=>!panelProbe.pins()[id]',extra_id), 'org change leaked extra geometry'
            page.wait_for_function('gitPending===0',polling=50)
            results.append('centered reference navigation closes its panel; pinned/detached navigation preserves it; source seed beats persisted default; close/org change remove extra pin geometry; insecure-context ID fallback opens panels')
            # Actual combined capture survives an explicit refresh pending across detachment,
            # then a failure. Its original next-page cursor must remain usable.
            page.goto(f'http://127.0.0.1:{server.server_port}')
            page.get_by_role('button',name='Open Git repositories',exact=True).click()
            first=page.locator('.git-workspace');graph_ready(first)
            active=first.get_by_label('Repository',exact=True).input_value()
            original_capture=next(r['response'] for r in reversed(requests) if r['path'].endswith('/'+active+'/snapshot'))
            next_cursor=original_capture['history']['next_cursor'];assert next_cursor,'INERT paging fixture'
            first.locator('.git-viewport').evaluate('e=>{e.scrollTop=700;e.scrollLeft=0}')
            page.clock.run_for(32)
            page.evaluate('() => { window.retainedViewport=document.querySelector(".git-viewport") }')
            before=first.locator('.git-viewport').evaluate('e=>({top:e.scrollTop,left:e.scrollLeft})')
            held_snapshot['rid']=active
            first.locator('.git-toolbar').get_by_role('button',name='Refresh',exact=True).click()
            assert held_snapshot['entered'].wait(5),'INERT refresh hold'
            first.locator('.git-loading').wait_for()
            assert page.evaluate('retainedViewport===document.querySelector(".git-viewport")')
            assert first.locator('.git-viewport').evaluate('e=>({top:e.scrollTop,left:e.scrollLeft})')==before
            assert first.locator('.git-node').count()>0,'pending refresh removed visible nodes'
            with page.expect_popup() as popped:first.get_by_role('button',name='Open in new window',exact=True).click()
            child=popped.value;child.on('pageerror',lambda e:errors.append(str(e)))
            assert child.evaluate('opener.retainedViewport===document.querySelector(".git-viewport")')
            held_snapshot['release'].set();held_snapshot['rid']=None
            child.get_by_role('alert').filter(has_text='fixture refresh unavailable').wait_for()
            child.wait_for_function('[...document.querySelectorAll("link[rel=stylesheet]")].every(e=>e.sheet)',polling=50)
            page.clock.run_for(32)
            assert child.locator('.git-viewport').evaluate('e=>({top:e.scrollTop,left:e.scrollLeft})')==before,(before,child.locator('.git-viewport').evaluate('e=>({top:e.scrollTop,left:e.scrollLeft,sh:e.scrollHeight,ch:e.clientHeight})'))
            assert child.locator('.git-node').count()>0,'failed refresh removed visible nodes'
            child.set_viewport_size(dict(width=2000,height=1000));child.wait_for_timeout(250)
            vb=child.locator('.git-viewport').bounding_box();cb=child.locator('.git-canvas').bounding_box()
            assert cb['width']>=vb['width']-16 and child.locator('.git-viewport').evaluate('e=>e.scrollTop')==700,('failed refresh resizing',vb,cb)
            previous_pages=sum('/history?' in r['path'] for r in requests)
            child.locator('.git-viewport').evaluate('e=>e.scrollTop=e.scrollHeight-e.clientHeight')
            graph_ready(child.locator('.git-workspace'))
            page.wait_for_function('gitPending===0',polling=50)
            history_requests=[r for r in requests if '/history?' in r['path']]
            assert len(history_requests)>previous_pages,'failed refresh lost paging cursor'
            from urllib.parse import parse_qs
            assert parse_qs(urlsplit(history_requests[-1]['path']).query)['cursor']==[next_cursor],'cursor switched capture'
            # Shrinking selected history must clamp the old pan to the short graph and
            # render its real nodes; expanding it again keeps the correct viewport observer.
            child.get_by_role('button',name='Branches and history',exact=True).click()
            child.get_by_label('long',exact=True).uncheck()
            graph_ready(child.locator('.git-workspace'))
            child.get_by_role('button',name='Branches and history',exact=True).click()
            child.wait_for_timeout(200)
            assert child.locator('.git-viewport').evaluate('e=>e.scrollTop')==0,'short selection kept stale pan'
            assert child.locator('.git-node').count()>0,'short selection culled every node'
            child.set_viewport_size(dict(width=2400,height=1300));child.wait_for_timeout(200)
            vb=child.locator('.git-viewport').bounding_box();cb=child.locator('.git-canvas').bounding_box()
            assert abs(vb['width']-cb['width'])<2 and abs(vb['height']-cb['height'])<2,('selection resize',vb,cb)
            child.get_by_role('button',name='Branches and history',exact=True).click();child.get_by_label('long',exact=True).check()
            graph_ready(child.locator('.git-workspace'))
            child.get_by_role('button',name='Branches and history',exact=True).click()
            child.locator('.git-viewport').evaluate('e=>e.scrollTop=900')
            other=second if active==a.rid else a.rid
            child.get_by_label('Repository',exact=True).select_option(other)
            graph_ready(child.locator('.git-workspace'))
            child.wait_for_timeout(100)
            assert child.locator('.git-viewport').evaluate('e=>e.scrollTop')==0,'repository switch failed to reset position'
            assert child.locator('.git-node').count()>0,'repository reset left blank viewport'
            child.screenshot(path=str(out/'captured-combined.png'))
            child.close();page.wait_for_timeout(100)
            if first.get_by_role('button',name='pin this to the window',exact=True).count():
                first.get_by_role('button',name='pin this to the window',exact=True).click()
            first.get_by_role('button',name='close this window',exact=True).click()
            results.append('pending refresh detaches with same DOM/pan; failure retains graph and original paging cursor; branch shrink and repository switch render correct reset; resizing still observes child')
            page.goto(f'http://127.0.0.1:{server.server_port}/?desk=1')
            page.locator('[data-mail-id="width-row"]').wait_for()
            with page.expect_popup() as popped:page.get_by_role('button',name='Open in new window',exact=True).click()
            child=popped.value; desk=[]
            for width in [1600,2200]:
                child.set_viewport_size(dict(width=width,height=1000));child.wait_for_timeout(300)
                boxes=child.locator('.desk-body,.msgs,[data-mail-id="width-row"],.msg.assistant').evaluate_all('es=>es.map(e=>({c:e.className,w:e.getBoundingClientRect().width}))')
                assert len(boxes)>=4 and all(e['w']>width-80 for e in boxes),(width,boxes)
                desk.append(dict(window=width,content=boxes))
            child.close();browser.close()
            assert not errors,errors
        print(json.dumps(dict(passed=results,git_widths=widths,typed_desk_widths=desk,data=str(DATA),aborted_on_close=aborted,polling=dict(busy_poll=busy_poll,cadence=cadence,same_repo=same_repo,different_repo=different_repo,before_detach=before_detach,detached=detached_poll,returned=returned_poll,last_b_close=last_b_close,last_close=last_close)),indent=2))
    finally:
        held_snapshot['release'].set();server.shutdown();thread.join();server.server_close();gw.scheduler.stop();client.close()

if __name__=='__main__':run()
