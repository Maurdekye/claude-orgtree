"""hoverbridge_probe.py — UX7: does the transparent "hover bridge" beside a
card (cards.tsx, added 2026-08-28 to keep the side hire-chips reachable — see
the ".hsof-bridge" comment in styles.css) intercept clicks meant for the open
canvas beside the card, turning a would-be canvas pan into a node drag?

THE SUSPECTED MECHANISM, stated precisely so it can be measured rather than
reasoned about:

  * `.sq > .hsof-bridge` is `position: absolute`, sized to the exact gap
    between the card and its neighbouring furniture (the credit bar on the
    left, open canvas or doc-chips on the right), `z-index: 1`, and —
    critically — `pointer-events: auto` UNCONDITIONALLY (styles.css
    ~line 2265). It is NOT gated on `.sq:hover` the way the chips themselves
    are; only the *chips* fade in/out and gain/lose pointer-events on hover.
    So the bridge is a live hit target at all times, whether or not the
    card is being hovered, and whether or not the hire chips are showing.

  * The bridge is a DOM CHILD of `.sq`, even though it is positioned
    OUTSIDE `.sq`'s own visual box (`right: 100%` / `left: 100%`). A
    pointerdown landing on it is therefore a pointerdown on a descendant of
    `.sq`, and — because React's synthetic events bubble — it reaches
    `.sq`'s own `onPointerDown` exactly as if the user had pressed the card
    itself.

  * `OrgCanvas.tsx`'s `startNodeDrag` (bound to `.sq`'s `onPointerDown`)
    excludes a short list of descendant selectors from starting a node drag:
    `button, input, textarea, select, .cbar, .desk-body`. `.hsof-bridge` is
    NOT on that list. So a press that lands in the open-canvas sliver this
    bridge covers — which visually looks like empty canvas next to the card,
    not the card itself — calls `e.stopPropagation()` and
    `setPointerCapture`, and drags the ENTIRE org subtree rooted at that
    card, exactly as if the user had grabbed the card's own body.

WHY A REAL BROWSER AGAINST A REAL (THROWAWAY) BACKEND, not a CSS-only fixture
like chipbar_probe.py next door. The defect is in POINTER EVENT BUBBLING and
React's own onPointerDown/stopPropagation/setPointerCapture chain in
OrgCanvas.tsx — none of that exists in a static HTML+CSS mockup. This drives
the real app, the real DOM, the real event handlers, against a disposable
backend/org that is deleted at the end of the run. It never touches port
7360 (the operator's live backend, confirmed listening — see breadcrumbs)
or any real org.

WHAT IS MEASURED
  1. STATIC HIT-TEST (like chipbar_probe's HIT): with the cursor parked at
     the centre of the open-canvas sliver the bridge covers (the part of
     `.hsof-bridge.bridge-l` that does NOT overlap `.cbar`), what does
     `document.elementFromPoint` return? Expect (if the bug is real): an
     element whose nearest identifying ancestor is `.hsof-bridge`/`.sq`, not
     the canvas/world layer.

  2. POSITIVE CONTROL — proves the probe can see a REAL pan at all. A
     press-move-release gesture starting on open canvas FAR from any card
     (no bridge anywhere near it) must move the camera (`.space`'s
     translate). If this fails, nothing below means anything: the probe
     cannot tell a working pan from a broken one.

  3. REPRODUCTION — the same press-move-release gesture, this time started
     at the exact point from (1), just left of the hired node's card. If the
     bridge is intercepting: the CARD's own screen position moves (it got
     dragged) and the CAMERA does NOT pan. If the bridge is not the problem:
     the camera pans exactly as in (2) and the card's position (relative to
     the camera) does not change.

  4. A THIRD point, just outside the bridge's own width (further left, on
     canvas the bridge does not claim) is also gestured, to show the
     interception is confined to the bridge's own footprint and not a wider
     breakage of panning near cards in general.

Both `.hsof-bridge.bridge-l` and `.bridge-r` are checked (bare card, no
documents, no desk fill — the common case).

USAGE
    python frontend/tests/hoverbridge_probe.py
    python frontend/tests/hoverbridge_probe.py --shot out.png

Requires playwright (msedge channel), fastapi, uvicorn, websockets — same
requirement as live_probe.py next door. Starts its own backend on a
dedicated port with a throwaway ORGTREE_DATA/HOME; never rebuilds
frontend/dist beyond what is already built; deletes the org it creates.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.normpath(os.path.join(_HERE, "..", ".."))

from playwright.sync_api import sync_playwright  # noqa: E402

# --------------------------------------------------------------------- rig
PORT = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 7419
BASE = f"http://127.0.0.1:{PORT}"

TMP = tempfile.mkdtemp(prefix="orgtree-hoverbridge-probe-")
HOME = os.path.join(TMP, "home")
DATA = os.path.join(TMP, "data")
CFG = os.path.join(TMP, "fakecli.json")
LOG = os.path.join(TMP, "backend.log")
os.makedirs(HOME, exist_ok=True)
os.makedirs(DATA, exist_ok=True)

# D-199 fixture: an isolated HOME has no detected Claude, and the hire gate
# refuses a Claude tier hire on a machine with none — see live_probe.py's
# identical comment for the full mechanism.
with open(os.path.join(HOME, ".claude.json"), "w", encoding="utf-8") as _f:
    json.dump({"oauthAccount": {
        "accountUuid": "hoverbridge-probe-uuid",
        "emailAddress": "hoverbridge-probe@example.test",
    }}, _f)

PROC: subprocess.Popen | None = None
_ORGS: list[str] = []


def _log_tail(n: int = 3000) -> str:
    try:
        return open(LOG, encoding="utf-8", errors="replace").read()[-n:]
    except OSError:
        return "(no log)"


def api(method: str, path: str, body=None, timeout: float = 30):
    req = urllib.request.Request(
        BASE + path, method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
    return json.loads(raw) if raw else {}


def set_cfg(**default) -> None:
    with open(CFG, "w", encoding="utf-8") as f:
        json.dump({"default": default}, f)


def start_backend() -> None:
    """Same rig as live_probe.py's start_backend(): its own ORGTREE_DATA/HOME,
    its own port, ORGTREE_CLAUDE_CLI -> fakecli.js, ORGTREE_BRIDGE_PORT=0 so
    the sandbox-bridge listener never contests the real one."""
    global PROC
    env = dict(os.environ)
    env.update({
        "ORGTREE_DATA": DATA, "USERPROFILE": HOME, "HOME": HOME,
        "ORGTREE_PORT": str(PORT),
        "FAKECLI_CONFIG": CFG,
        "ORGTREE_MAX_TURNS": "16",
        "ORGTREE_STEER_HOOK": "0",
        "ORGTREE_TURN_TIMEOUT": "60",
        "PYTHONPATH": os.path.join(_REPO, "backend"),
        "PYTHONIOENCODING": "utf-8",
        "ORGTREE_BRIDGE_PORT": "0",
        "ORGTREE_CLAUDE": os.path.join(_REPO, "backend", "tests", "fakecli.js"),
        "ORGTREE_CLAUDE_CLI": os.path.join(_REPO, "backend", "tests", "fakecli.js"),
    })
    env.pop("ORGTREE_PUBLIC_PORT", None)
    env.pop("ORGTREE_EXPOSE_ADMIN", None)
    set_cfg(replyText="ack.")
    log = open(LOG, "a", encoding="utf-8")
    PROC = subprocess.Popen(
        [sys.executable, "-m", "orgtree.api"], cwd=os.path.join(_REPO, "backend"),
        env=env, stdout=log, stderr=log, text=True)
    for _ in range(200):
        if PROC.poll() is not None:
            raise RuntimeError(
                f"backend exited with {PROC.returncode} during startup; "
                f"log tail:\n" + _log_tail())
        try:
            urllib.request.urlopen(BASE + "/api/orgs", timeout=1).read()
            print(f"backend up on :{PORT}  (data={DATA}  home={HOME})")
            return
        except Exception:                                        # noqa: BLE001
            time.sleep(0.1)
    raise RuntimeError(f"backend did not come up on {PORT}:\n" + _log_tail())


def stop_backend() -> None:
    global PROC
    if PROC is None:
        return
    PROC.terminate()
    try:
        PROC.wait(timeout=10)
    except subprocess.TimeoutExpired:
        PROC.kill()
    PROC = None


def make_org(label: str) -> str:
    r = api("POST", "/api/orgs", {"name": f"zz hoverbridge-probe {label}"[:60]})
    slug = r.get("slug") or r.get("org", {}).get("slug")
    _ORGS.append(slug)
    return slug


def hire(slug: str, name: str) -> str:
    r = api("POST", f"/api/orgs/{slug}/ops", {
        "op": "hire", "actor": "@user", "parent": None, "tier": "haiku",
        "grant": 2, "name": name, "charter": "a hoverbridge-probe test agent",
        "tools": {"bash": False, "web": False, "edit": False,
                  "subagents": False, "mcp": []},
        "org_visibility": "team", "add_dirs": []})
    return r.get("node") or name


def drop_orgs() -> None:
    for slug in list(_ORGS):
        try:
            api("DELETE", f"/api/orgs/{slug}")
        except Exception as e:                                    # noqa: BLE001
            print(f"  (cleanup) failed to delete {slug}: {e}")


RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


CAM = """
() => {
  const sp = document.querySelector('.space');
  if (!sp) return null;
  const m = /translate\\(\\s*(-?[\\d.]+)px\\s*,\\s*(-?[\\d.]+)px\\s*\\)/.exec(sp.style.transform);
  return m ? [Number(m[1]), Number(m[2])] : null;
}
"""

RECTS = """
(nodeName) => {
  const r = (el) => { if (!el) return null; const b = el.getBoundingClientRect();
    return {x: b.x, y: b.y, w: b.width, h: b.height, right: b.right, bottom: b.bottom}; };
  const sq = Array.from(document.querySelectorAll('.sq'))
    .find(el => el.querySelector(`.name`) && el.querySelector('.name').textContent === nodeName);
  const bridgeL = sq ? sq.querySelector('.hsof-bridge.bridge-l') : null;
  const bridgeR = sq ? sq.querySelector('.hsof-bridge.bridge-r') : null;
  const cbar = sq ? sq.querySelector('.cbar') : null;
  return { sq: r(sq), bridgeL: r(bridgeL), bridgeR: r(bridgeR), cbar: r(cbar) };
}
"""

HIT = """
(pt) => {
  const el = document.elementFromPoint(pt.x, pt.y);
  if (!el) return {tag: null, cls: null, owner: null};
  const own = el.closest('.hsof-bridge, .cbar, .hsof, .sq, .viewport, .space');
  return {tag: el.tagName.toLowerCase(), cls: el.className || null,
          owner: own ? own.className : null};
}
"""


def gesture(pg, x0, y0, dx=120, dy=60, steps=8):
    """Press at (x0,y0), drag by (dx,dy) over `steps` real pointermoves,
    release. Returns nothing; caller reads state before/after."""
    pg.mouse.move(x0, y0)
    pg.mouse.down()
    pg.wait_for_timeout(30)
    for k in range(1, steps + 1):
        pg.mouse.move(x0 + dx * k / steps, y0 + dy * k / steps)
        pg.wait_for_timeout(15)
    pg.wait_for_timeout(30)
    pg.mouse.up()
    pg.wait_for_timeout(60)


def run(shot: str | None) -> int:
    start_backend()
    try:
        slug = make_org("ux7")
        node = hire(slug, "bridgee")
        print(f"  org={slug} node={node}")

        with sync_playwright() as p:
            br = p.chromium.launch(channel="msedge", headless=True)
            pg = br.new_page(viewport={"width": 1600, "height": 950})
            pg.goto(f"{BASE}/o/{slug}")
            pg.wait_for_selector(".sq", timeout=10000)
            pg.wait_for_timeout(1000)   # spring settle, matches ui_probe.py

            rects = pg.evaluate(RECTS, node)
            if not rects["sq"] or not rects["bridgeL"] or not rects["bridgeR"] \
                    or not rects["cbar"]:
                raise SystemExit(
                    "fixture did not render .sq/.hsof-bridge.bridge-l/"
                    ".bridge-r/.cbar for the hired node — nothing below would "
                    "mean anything. rects=" + json.dumps(rects))

            cam0 = pg.evaluate(CAM)
            print(f"  camera before any gesture: {cam0}")

            # -------------------------------------------------- (2) control
            # open canvas far from the card and from the @user root: a
            # generous offset up-and-left of the hired card's own rect.
            far_x = max(20.0, rects["sq"]["x"] - 260)
            far_y = max(20.0, rects["sq"]["y"] - 200)
            print(f"\n  == positive control: drag from open canvas "
                  f"({far_x:.0f},{far_y:.0f}) far from any card ==")
            cam_before = pg.evaluate(CAM)
            gesture(pg, far_x, far_y)
            cam_after = pg.evaluate(CAM)
            panned = cam_before != cam_after and cam_after is not None
            check("CONTROL: drag on open canvas (far from any card) pans "
                  "the camera — proves the probe can see a working pan",
                  panned, f"{cam_before} -> {cam_after}")
            if not panned:
                print("  ABORT: the positive control itself failed — a pan "
                      "cannot be told from a broken one here, so the "
                      "reproduction below would prove nothing.")
                return 1

            # re-fetch rects: panning moved the card on screen
            rects = pg.evaluate(RECTS, node)

            # -------------------------------------------------- (1) hit test
            # the open-canvas sliver the bridge covers but .cbar does not:
            # between cbar's right edge and the card's own left edge.
            bl, cb, sq = rects["bridgeL"], rects["cbar"], rects["sq"]
            sliver_x = (cb["right"] + sq["x"]) / 2
            sliver_y = sq["y"] + sq["h"] / 2
            print(f"\n  == static hit-test at the open-canvas sliver beside "
                  f"the card ({sliver_x:.1f},{sliver_y:.1f}) ==")
            hit = pg.evaluate(HIT, {"x": sliver_x, "y": sliver_y})
            print(f"  elementFromPoint owner: {hit['owner']!r}")
            check("HIT-TEST: the point between the credit bar and the card's "
                  "own edge is claimed by .hsof-bridge (not by the canvas)",
                  bool(hit["owner"]) and "hsof-bridge" in hit["owner"],
                  f"owner={hit['owner']!r}")

            # -------------------------------------------------- (3) repro
            print(f"\n  == reproduction: drag starting at that same point ==")
            cam_before = pg.evaluate(CAM)
            sq_before = pg.evaluate(RECTS, node)["sq"]
            gesture(pg, sliver_x, sliver_y)
            cam_after = pg.evaluate(CAM)
            sq_after = pg.evaluate(RECTS, node)["sq"]
            cam_moved = cam_before != cam_after
            cam_dx = (cam_after[0] - cam_before[0]) if (cam_before and cam_after) else 0.0
            cam_dy = (cam_after[1] - cam_before[1]) if (cam_before and cam_after) else 0.0
            card_dx = sq_after["x"] - sq_before["x"]
            card_dy = sq_after["y"] - sq_before["y"]
            # a pure camera pan shifts every card's SCREEN rect by exactly the
            # camera's own delta; a node drag moves the card BEYOND that (or
            # instead of it, if the drag captured the pointer and no pan
            # happened at all). Comparing the card's shift to the camera's
            # shift is what tells the two apart — a raw "did the screen rect
            # move" check can't, since a working pan also moves every card.
            card_dragged = abs(card_dx - cam_dx) > 2 or abs(card_dy - cam_dy) > 2
            print(f"  camera delta: ({cam_dx:.1f},{cam_dy:.1f})")
            print(f"  card screen delta: ({card_dx:.1f},{card_dy:.1f})  "
                  f"(beyond camera pan alone: {card_dragged})")
            check("REPRO: a press-drag starting in the bridge's open-canvas "
                  "sliver pans the camera exactly like the control did "
                  "(the click reached the canvas, not the card)",
                  cam_moved, f"camera moved={cam_moved}")
            check("REPRO: the card's screen position moves BY NO MORE than "
                  "the camera's own pan — i.e. the node itself was not "
                  "dragged independently of the camera",
                  not card_dragged,
                  f"card delta ({card_dx:.1f},{card_dy:.1f}) vs camera delta "
                  f"({cam_dx:.1f},{cam_dy:.1f})")

            # ---------------------------------------- (4) confinement check
            outside_x = bl["x"] - 15
            outside_y = sq["y"] + sq["h"] / 2
            print(f"\n  == confinement: drag from just OUTSIDE the bridge's "
                  f"own width ({outside_x:.1f},{outside_y:.1f}) ==")
            cam_before = pg.evaluate(CAM)
            gesture(pg, outside_x, outside_y)
            cam_after = pg.evaluate(CAM)
            panned2 = cam_before != cam_after
            check("CONFINEMENT: just outside the bridge's own footprint, "
                  "panning works normally again (the interception is "
                  "confined to the bridge's own rect, not a wider breakage)",
                  panned2, f"{cam_before} -> {cam_after}")

            if shot:
                pg.screenshot(path=shot)
                print(f"\n  screenshot saved: {shot}")

            br.close()
    finally:
        drop_orgs()
        stop_backend()

    fails = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n  {len(RESULTS) - len(fails)}/{len(RESULTS)} checks passed")
    if fails:
        print("  FAILURES (recall: some of these are EXPECTED to fail if the "
              "bug is real — this script reports what it measured, not a "
              "verdict):")
        for f in fails:
            print("   - " + f)
    return 0


def main() -> int:
    shot = None
    if "--shot" in sys.argv:
        shot = sys.argv[sys.argv.index("--shot") + 1]
    return run(shot)


if __name__ == "__main__":
    raise SystemExit(main())
