"""Actual application Runtime toggle; all API reads/writes are fixture responses."""
import argparse,json
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright

parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
bundle=Path(__file__).resolve().parents[1]/'node_modules/.orgtree-git-settings'
assert (bundle/'probe.js').is_file(),'INERT: build fixture first'
args.out.mkdir(parents=True,exist_ok=True)
enabled=False;calls=[];errors=[];blocked=[]
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,executable_path='C:/Users/ncola_k8bx/AppData/Local/ms-playwright/chromium-1243/chrome-win64/chrome.exe')
    page=browser.new_page(viewport={'width':1200,'height':900})
    page.add_init_script("navigator.sendBeacon=()=>false;window.WebSocket=class{constructor(){throw Error('blocked')}}")
    def route(r):
        global enabled
        path=urlsplit(r.request.url).path;method=r.request.method
        assert urlsplit(r.request.url).netloc=='git-settings.test'
        if path=='/' and method=='GET':
            r.fulfill(content_type='text/html',body='<link rel="stylesheet" href="/probe.css"><div id="root"></div><script type="module" src="/probe.js"></script>');return
        if path in ('/probe.css','/probe.js') and method=='GET':
            r.fulfill(content_type='text/css' if path.endswith('css') else 'text/javascript',body=(bundle/path[1:]).read_bytes());return
        calls.append({'path':path,'method':method,'body':r.request.post_data})
        if path=='/api/app-settings/runtime' and method in ('GET','PUT'):
            if method=='PUT':
                body=json.loads(r.request.post_data);assert set(body)=={'git_periodic_fetch_enabled'}
                enabled=body['git_periodic_fetch_enabled']
            value={'warming_enabled':True,'working_checkups_enabled':True,'wait_for_mcp_tools_enabled':False,'idle_docket_reminders_enabled':False,'git_periodic_fetch_enabled':enabled}
        elif path=='/api/accounts' and method=='GET':value={'primary':{'signed_in':False},'keys':[]}
        elif path=='/api/providers' and method=='GET':value={'providers':[]}
        elif path.endswith('/observation') and method=='GET':value={'busy':False,'freshness':{'watched':enabled}}
        elif path.endswith('/watch') and method=='POST':value={'started':True}
        else:blocked.append(path);r.abort();return
        r.fulfill(json=value)
    page.route('**/*',route);page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto('http://git-settings.test')
    page.evaluate("fetch('/api/write-control',{method:'POST'}).catch(()=>null)");assert blocked
    page.get_by_role('tab',name='Runtime',exact=False).click()
    checkbox=page.get_by_role('switch',name='fetch open repositories every 30 seconds',exact=True)
    checkbox.wait_for();assert not checkbox.is_checked()
    checkbox.click();page.wait_for_function('window.gitSettingsSeen.every(values=>values.at(-1)===true)')
    assert checkbox.is_checked()
    assert len([c for c in calls if c['path'].endswith('/watch')])==1,'Two views must share the watch request'
    page.screenshot(path=str(args.out/'app-settings-fetch.png'))
    checkbox.click();page.wait_for_function('window.gitSettingsSeen.every(values=>values.at(-1)===false)')
    assert not checkbox.is_checked() and not enabled
    assert not errors,errors
    result={'calls':calls,'blocked':blocked,'errors':errors,'seen':page.evaluate('window.gitSettingsSeen'),'default_off':True,'roundtrip':True}
    (args.out/'app-settings-fetch.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
    browser.close()

