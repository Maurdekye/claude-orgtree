"""UI probe — visual inspection + interaction with the running orgtree UI.

Drives the system-installed Edge via Playwright (channel="msedge", headless; no
browser download). Used for autonomous UI/UX iteration: run a flow, screenshot,
LOOK at the image, fix, repeat.

Usage:
    python tools/ui_probe.py overview <org> out.png
    python tools/ui_probe.py desk <org> <node> out.png     # zoom onto a desk
    python tools/ui_probe.py gear <org> <node> out.png     # open the ⚙ panel
    python tools/ui_probe.py inbox <org> out.png
    python tools/ui_probe.py hoverchips <org> <node> out.png
    python tools/ui_probe.py sweep <org> <outdir>          # EVERYTHING, one session
"""

import os
import sys
import time

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:7360"
VIEW = {"width": 1600, "height": 950}


def open_org(p, org):
    browser = p.chromium.launch(channel="msedge", headless=True)
    page = browser.new_page(viewport=VIEW)
    page.goto(f"{BASE}/o/{org}")
    page.wait_for_selector(".sq", timeout=8000)
    time.sleep(1.2)          # springs settle
    return browser, page


def card(page, node):
    return page.locator(f'.sq:has(.name:text-is("{node}"))').first


def wheel(page, x, y, clicks, per=-600):
    page.mouse.move(x, y)
    for _ in range(clicks):
        page.mouse.wheel(0, per)
        time.sleep(0.12)


def shot(page, out):
    page.screenshot(path=out)
    print("saved", out)



def close_overlays(page):
    for _ in range(4):
        if not page.locator(".overlay").count():
            return
    # backdrop click: bottom-left corner is never the centered panel
        page.mouse.click(12, VIEW["height"] - 12)
        time.sleep(0.35)
    page.evaluate("document.querySelectorAll('.overlay').forEach(o => o.remove())")
    time.sleep(0.2)


# ---------------------------------------------------------------- the sweep
def sweep(org, outdir):
    os.makedirs(outdir, exist_ok=True)
    o = lambda name: os.path.join(outdir, name + ".png")
    cx, cy = VIEW["width"] / 2, VIEW["height"] / 2
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport=VIEW)
        page.on("dialog", lambda d: d.dismiss())

        # 1 · welcome screen (no active org)
        page.goto(BASE + "/")
        page.wait_for_selector(".welcome-card", timeout=8000)
        time.sleep(0.4)
        shot(page, o("01-welcome"))

        # 2 · overview at fit-on-load
        page.goto(f"{BASE}/o/{org}")
        page.wait_for_selector(".sq", timeout=8000)
        time.sleep(1.4)
        shot(page, o("02-overview"))

        # 3 · org drawer
        page.locator(".orgbar .iconbtn").first.click()
        time.sleep(0.4)
        shot(page, o("03-drawer"))
        page.mouse.click(VIEW["width"] - 100, cy)   # close via backdrop
        time.sleep(0.3)

        # 4 · mini LOD (zoomed far out)
        wheel(page, cx, cy, 3, per=+600)
        time.sleep(0.5)
        shot(page, o("04-mini"))
        # 5 · back to norm-ish (fresh fit — wheel zoom drifts the view)
        page.goto(f"{BASE}/o/{org}")
        page.wait_for_selector(".sq", timeout=8000)
        time.sleep(1.3)
        shot(page, o("05-norm"))

        # 6 · hover chips on a live node
        c = card(page, "atlas")
        c.hover()
        time.sleep(0.4)
        shot(page, o("06-hoverchips-node"))

        # 7 · hover chips on the user eye + credit bar tooltip
        page.locator(".sq.user").hover()
        time.sleep(0.4)
        shot(page, o("07-hoverchips-eye"))
        card(page, "atlas").locator(".cbar").hover(force=True)
        time.sleep(0.4)
        shot(page, o("08-cbar-tip"))

        # 9 · draft box (hire under the eye)
        page.locator(".sq.user").hover()
        time.sleep(0.3)
        page.locator(".sq.user .hsof button").nth(1).click()   # S
        time.sleep(1.2)
        shot(page, o("09-draft"))
        page.keyboard.press("Escape")
        time.sleep(0.5)

        # 10 · desk (click atlas → camera glide, chat over card)
        page.goto(f"{BASE}/o/{org}")   # the draft glide moved the camera away
        page.wait_for_selector(".sq", timeout=8000)
        time.sleep(1.3)
        card(page, "atlas").click()
        time.sleep(1.5)
        shot(page, o("10-desk"))

        # 11-12 · desk tabs
        page.locator('.cc-tabs button:text-is("history")').click()
        time.sleep(0.6)
        shot(page, o("11-desk-history"))
        page.locator('.cc-tabs button:text-is("files")').click()
        time.sleep(0.6)
        shot(page, o("12-desk-files"))
        page.locator('.cc-tabs button:text-is("chat")').click()
        time.sleep(0.3)

        # 13 · credit-bar tip while the desk is open (left side, stacked)
        page.locator(".sq.desk").locator("..").locator(".sq.desk .cbar").hover(force=True)
        time.sleep(0.4)
        shot(page, o("13-desk-cbar-tip"))

        # 14 · gear panel
        page.locator(".sq.desk .cc-icon").click()
        time.sleep(0.6)
        shot(page, o("14-gear"))
        close_overlays(page)

        # 15 · lineage panel (sentinel has a 2-gen stack)
        page.goto(f"{BASE}/o/{org}")
        page.wait_for_selector(".sq", timeout=8000)
        time.sleep(1.3)
        shot(page, o("15-overview-again"))
        sq = card(page, "sentinel")
        bb = sq.bounding_box()
        wheel(page, bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2, 2)
        time.sleep(0.6)
        shot(page, o("15b-sentinel-zoomed"))
        # the desk is now open over sentinel — its gen badge opens the lineage
        page.locator(".sq.desk .stackbadge").click()
        time.sleep(0.5)
        shot(page, o("16-lineage"))
        close_overlays(page)

        # 17 · desk of a LIVE knowledge bearer (tethered card) — from overview,
        # where the bearer is on-screen
        page.goto(f"{BASE}/o/{org}")
        page.wait_for_selector(".sq", timeout=8000)
        time.sleep(1.3)
        b = card(page, "sentinel@1")
        if b.count():
            b.click()
            time.sleep(1.6)
            shot(page, o("17-bearer-desk"))

        # 18 · inbox (msgs + audience request + holders)
        page.locator(".inboxbtn").click()
        time.sleep(0.6)
        shot(page, o("18-inbox"))
        close_overlays(page)

        # 19 · org settings
        page.locator('.orgbar button:text-is("⚙ settings")').click()
        time.sleep(0.8)
        shot(page, o("19-settings"))
        close_overlays(page)

        # 20 · deep-chain desk at max zoom (d4)
        page.goto(f"{BASE}/o/{org}")
        page.wait_for_selector(".sq", timeout=8000)
        time.sleep(1.3)
        d4 = card(page, "d4")
        if d4.count():
            d4.click()
            time.sleep(1.5)
            wheel(page, cx, cy, 3)          # push past Z_FOCUS toward Z_MAX
            time.sleep(0.6)
            shot(page, o("20-maxzoom-desk"))

        # 21 · faked runtime looks: busy border + spinner + status chips
        page.goto(f"{BASE}/o/{org}")
        page.wait_for_selector(".sq", timeout=8000)
        time.sleep(1.3)
        page.evaluate("""() => {
          const cards = [...document.querySelectorAll('.sq')]
          const byName = (n) => cards.find(c =>
            c.querySelector('.name')?.textContent === n)
          const atlas = byName('atlas')
          if (atlas) {
            atlas.classList.add('busy')
            const b = atlas.querySelector('.sq-badges')
            if (b) b.insertAdjacentHTML('beforeend',
              '<span class="statuschip working">working</span>')
            const h = atlas.querySelector('.sq-head')
            if (h) h.insertAdjacentHTML('beforeend', '<span class="busydot"></span>')
          }
          const t = byName('tester')
          if (t) t.querySelector('.sq-badges')?.insertAdjacentHTML('beforeend',
            '<span class="statuschip done">done</span>')
          const bw = byName('builder-web')
          if (bw) bw.querySelector('.sq-badges')?.insertAdjacentHTML('beforeend',
            '<span class="statuschip blocked">blocked</span>')
        }""")
        time.sleep(0.3)
        shot(page, o("21-runtime-fakes"))

        browser.close()


def main():
    mode, org = sys.argv[1], sys.argv[2]
    out = sys.argv[-1]
    if mode == "sweep":
        sweep(org, out)
        return
    with sync_playwright() as p:
        browser, page = open_org(p, org)
        if mode == "overview":
            pass
        elif mode == "desk":
            node = sys.argv[3]
            card(page, node).click()
            time.sleep(1.4)                       # camera glide + desk fade
        elif mode == "deskfake":
            # open the desk and inject a fake conversation — previews message
            # styling without a real session
            node = sys.argv[3]
            card(page, node).click()
            time.sleep(1.5)
            page.evaluate("""() => {
              const m = document.querySelector('.msgs')
              if (!m) return
              m.innerHTML = `
                <div class="msg user"><div class="msgtext">Survey the repo and propose a plan for the importer refactor. Keep it small.</div></div>
                <div class="msg assistant"><div class="tools">⏺ Read(importer/core.py) · Grep(load_mesh)</div><div class="msgtext">The importer has two entry points; only one handles scaling. I'd unify them behind load_asset() and add a scale test. Three files change, ~60 lines.</div></div>
                <div class="msg user"><div class="msgtext">Go ahead, but keep the public API unchanged.</div></div>
                <div class="msg assistant live"><div class="msgtext">Refactoring now — unifying the entry points first…</div></div>
                <div class="msg live tools">⏺ Edit(importer/core.py)</div>
                <div class="working"><span class="cc-spin">✳</span> working<span class="actdots"></span></div>`
              m.scrollTop = m.scrollHeight
            }""")
            time.sleep(0.4)
        elif mode == "gear":
            node = sys.argv[3]
            c = card(page, node)
            c.hover()
            time.sleep(0.3)
            c.locator(".gearbtn").click()
            time.sleep(0.5)
        elif mode == "inbox":
            page.locator(".inboxbtn").click()
            time.sleep(0.6)
        elif mode == "hoverchips":
            node = sys.argv[3]
            card(page, node).hover()
            time.sleep(0.4)
        page.screenshot(path=out)
        browser.close()
    print("saved", out)


if __name__ == "__main__":
    main()
