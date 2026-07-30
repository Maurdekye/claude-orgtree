"""Regenerate social-preview.png — THE defined process (user ruling).

Spins an isolated throwaway backend, builds the canonical demo org
(orchestrator -> implementer + planner -> explorer x2), lets the intro
animation settle to the full-tree fit, then zooms IN by 1/0.72 about the
viewport centre — the content fills ~72% more of the frame than a plain fit,
killing the unnecessary margins — and screenshots at 2560x1280 (GitHub's
social-preview aspect). Upload is manual: repo Settings -> Social preview
(GitHub has no API for it).

    python tools/social_preview.py          # writes ./social-preview.png
"""
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND = os.path.join(REPO, "backend")
PORT = "7397"
DATA = os.path.join(tempfile.gettempdir(), "orgtree-social-preview")
OUT = os.path.join(REPO, "social-preview.png")
# The ruling asked 72%, but at exactly 0.72 the (portrait) tree overflows the
# 2:1 frame by ~120px — either the eye or the explorer row clips. 0.77 is the
# closest scale where everything fits with slim margins; tweak if the cast
# ever changes shape.
CONTENT_SCALE = 0.77


def call(m, p, b=None):
    d = json.dumps(b).encode() if b is not None else None
    r = urllib.request.Request(f"http://127.0.0.1:{PORT}{p}", data=d, method=m,
                               headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(r, timeout=25))


shutil.rmtree(DATA, ignore_errors=True)
os.makedirs(DATA, exist_ok=True)
env = dict(os.environ)
env.update(ORGTREE_DATA=DATA, ORGTREE_PORT=PORT, ORGTREE_BRIDGE_PORT="0")
proc = subprocess.Popen([sys.executable, "-m", "orgtree.api"], cwd=BACKEND,
                        env=env, stdout=subprocess.DEVNULL,
                        stderr=subprocess.STDOUT)
try:
    for _ in range(40):
        time.sleep(0.5)
        try:
            call("GET", "/api/orgs")
            break
        except Exception:
            pass
    call("POST", "/api/orgs", {"name": "acme"})
    call("POST", "/api/orgs/acme/ops",
         {"op": "hire", "tier": "opus", "name": "orchestrator", "grant": 12})
    call("POST", "/api/orgs/acme/ops",
         {"op": "hire", "parent": "orchestrator", "tier": "sonnet",
          "name": "implementer", "grant": 4})
    call("POST", "/api/orgs/acme/ops",
         {"op": "hire", "parent": "orchestrator", "tier": "sonnet",
          "name": "planner", "grant": 2})
    for nm in ("explorer", "explorer-b"):
        call("POST", "/api/orgs/acme/ops",
             {"op": "hire", "parent": "implementer", "tier": "haiku",
              "name": nm, "grant": 0})

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="msedge", headless=True)
        pg = b.new_page(viewport={"width": 2560, "height": 1280})
        pg.goto(f"http://127.0.0.1:{PORT}/o/acme", wait_until="networkidle")
        time.sleep(3.0)                      # intro glide settles on fitAll
        # zoom IN by 1/CONTENT_SCALE about the viewport centre: the canvas
        # zoom factor per wheel event is e^(-deltaY * 0.0012)
        pg.mouse.move(1280, 640)
        pg.mouse.wheel(0, -math.log(1 / CONTENT_SCALE) / 0.0012)
        time.sleep(0.5)
        # the fit reserves headroom above the eye's infinite bar, which biases
        # content downward — pan up so the bottom row clears the frame edge.
        # ⚠ drag from EMPTY canvas: the viewport centre sits on a card, and
        # grabbing a card drags the node, not the camera
        pg.mouse.move(300, 640)
        pg.mouse.down()
        pg.mouse.move(300, 540, steps=8)
        pg.mouse.up()
        # park the cursor over empty canvas: hovering a card would bake its
        # H/S/O/F hire chips into the shot
        pg.mouse.move(150, 1150)
        time.sleep(0.9)
        pg.screenshot(path=OUT)
        b.close()
    print("wrote", OUT)
    print("upload manually: GitHub repo Settings -> Social preview")
finally:
    proc.terminate()
    proc.wait(timeout=10)
    shutil.rmtree(DATA, ignore_errors=True)
