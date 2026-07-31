"""Regenerate social-preview.png — THE defined process (user ruling).

Spins an isolated throwaway backend, builds the canonical demo org
(user ruling 2026-07-31):

    coordinator<opus>
    ├── implementer<fable>
    └── researcher<opus>
        ├── explorer-1<sonnet>
        └── explorer-2<sonnet>

lets the intro animation settle to the full-tree fit, then zooms about the
viewport centre to CONTENT_SCALE and screenshots at 2560x1280 (GitHub's
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
# < 1 zooms IN past the plain fit (kills margin); the user asked for the
# agents a bit further OUT than the old 0.77 — 0.88 keeps slim margins with
# visibly smaller cards. Tweak if the cast ever changes shape.
CONTENT_SCALE = 0.88


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
    # coordinator holds: own grant 24 = implementer's fable seat 10
    # + researcher's opus seat 5 + researcher's grant 6 (2 sonnet seats),
    # leaving 3 free — a small uncommitted sliver so the bar reads live
    call("POST", "/api/orgs/acme/ops",
         {"op": "hire", "tier": "opus", "name": "coordinator", "grant": 24})
    call("POST", "/api/orgs/acme/ops",
         {"op": "hire", "parent": "coordinator", "tier": "fable",
          "name": "implementer", "grant": 0})
    call("POST", "/api/orgs/acme/ops",
         {"op": "hire", "parent": "coordinator", "tier": "opus",
          "name": "researcher", "grant": 6})
    for nm in ("explorer-1", "explorer-2"):
        call("POST", "/api/orgs/acme/ops",
             {"op": "hire", "parent": "researcher", "tier": "sonnet",
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
