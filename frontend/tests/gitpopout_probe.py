"""Actual landed GitWorkspace combined with private popouts; no backend or Git writes."""
import functools, http.server, json, os, threading
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'node_modules' / '.orgtree-git-popout'
os.environ['ORGTREE_DATA'] = str(OUT / 'throwaway-data')
assert Path(os.environ['ORGTREE_DATA']).resolve() != Path.home() / 'orgtree'
class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_): pass

def run():
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Quiet, directory=str(OUT)))
    thread = threading.Thread(target=server.serve_forever); thread.start()
    results = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='msedge')
            context = browser.new_context(viewport={'width':1100, 'height':850})
            context.set_default_timeout(5000)
            page = context.new_page()
            page.on('pageerror', lambda e: print('PAGE ERROR:', e, flush=True))
            page.goto(f'http://127.0.0.1:{server.server_port}')
            page.locator('.git-viewport').wait_for()
            page.evaluate('window.originalGit = document.querySelector(".git-viewport")')
            with page.expect_popup() as opened:
                page.get_by_role('button', name='Open in new window', exact=True).click()
            child = opened.value
            child.on('pageerror', lambda e: print('CHILD ERROR:', e, flush=True))
            child.locator('.git-viewport').wait_for()
            for width, height in [(650,650),(1600,1100)]:
                child.set_viewport_size({'width':width,'height':height})
                child.locator('.git-viewport').evaluate('''e => {
                    const r=e.getBoundingClientRect();
                    e.scrollLeft=Math.max(0,1450+r.left-(r.right-35));
                    e.scrollTop=Math.max(0,1625+r.top-(r.bottom-30));
                }''')
                child.wait_for_timeout(250)  # commit the graph's intentional scroll/resize events
                node = child.locator('.git-node')
                node.wait_for()
                box = node.bounding_box()
                assert node.evaluate('e=>e.ownerDocument.elementFromPoint(e.getBoundingClientRect().x+12,e.getBoundingClientRect().y+12)?.closest(".git-node")===e'), 'target is clipped or covered'
                assert box['x'] > width * .65 and box['y'] > height * .55, box
                child.mouse.move(10, 200)
                child.mouse.move(box['x']+12, box['y']+12)
                child.wait_for_timeout(350)
                hover = child.locator('.git-hover')
                hover.wait_for()
                h = hover.bounding_box()
                assert h['x'] >= 7 and h['y'] >= 7 and h['x']+h['width'] <= width-7 and h['y']+h['height'] <= height-7, (width,h)
                expected_x=max(8,min(box['x']+box['width']+12,width-h['width']-8))
                expected_y=max(8,min(box['y'],height-h['height']-8))
                assert abs(h['x']-expected_x)<2 and abs(h['y']-expected_y)<2, ('hover used wrong window',width,h,expected_x,expected_y)
                assert hover.evaluate('e=>e.scrollHeight>e.clientHeight'), 'long content must actually exercise height limit'
                # The clamped tooltip covers this extreme-edge node. Keyboard activation
                # exercises its action without locator-induced scrolling or clicking the tooltip.
                node.evaluate('e=>e.focus({preventScroll:true})')
                child.keyboard.press('Enter')
                action = child.locator('.git-node-action')
                action.wait_for()
                a=action.bounding_box()
                assert a['x']>=7 and a['y']>=7 and a['x']+a['width']<=width-7 and a['y']+a['height']<=height-7, (width,a)
                assert width < 1100 or a['x'] > 1100, ('larger child action must use space beyond opener width',a)
                child.screenshot(path=str(OUT/f'git-child-{width}.png'))
                action.get_by_role('button', name='Push local changes', exact=True).click()
                child.locator('.git-node-action').wait_for(state='detached')
                assert page.evaluate('originalGit.isConnected && originalGit.ownerDocument !== document')
                assert page.locator('.git-viewport').count()==0
                results.append({'child':[width,height],'target':box,'hover':h,'action':a})
            # Real middle-node mouse activation, with no locator scroll or keyboard fallback.
            child.locator('.git-viewport').evaluate('e=>{ e.scrollLeft=1150; e.scrollTop=1200 }')
            child.wait_for_timeout(250)
            box=child.locator('.git-node').bounding_box()
            child.mouse.move(10,200)
            child.mouse.move(box['x']+12,box['y']+12)
            child.locator('.git-hover').wait_for()
            assert child.locator('.git-node').evaluate('e=>e.ownerDocument.elementFromPoint(e.getBoundingClientRect().x+12,e.getBoundingClientRect().y+12)?.closest(".git-node")===e')
            child.mouse.click(box['x']+12,box['y']+12)
            child.locator('.git-node-action').wait_for()
            child.get_by_role('button',name='Push local changes',exact=True).click()
            child.locator('.git-node-action').wait_for(state='detached')
            assert page.evaluate("gitPopout.requests.filter(u=>u.endsWith('/push')).length === 3")
            child.close()
            page.locator('.git-viewport').wait_for()
            assert page.evaluate('document.querySelector(".git-viewport") === originalGit')
            browser.close()
        print(json.dumps({'source':json.loads((OUT/'source.json').read_text()),'passed':results,'middle_mouse_push':True,'same_node_return':True},indent=2))
    finally:
        server.shutdown();thread.join();server.server_close()
if __name__=='__main__':run()
