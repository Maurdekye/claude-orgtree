"""Rendered left-edge regression: the leftmost branch label is reachable.

User screenshot 2026-09-07 11:00Z: with four side lanes the far-left branch
annotation was clipped off the canvas and no drag could reveal it. This
probe builds a real repository with FIVE branches (four side lanes), opens
the Git workspace in real Chromium against isolated fixture API, scrolls the
viewport fully left, and asserts that every annotation and every node lies
inside the canvas: bounding box left >= the viewport's left edge. It also
scrolls fully right and asserts nothing is past the canvas width.

Positive control (the instrument can fail): `--old-bundle <dir>` names a
second fixture bundle built from the PRE-FIX layout (frontend/src/git/
layout.ts as landed before this change). The same fixture is loaded with
that bundle and the leftmost label must then be OUTSIDE the viewport at
scrollLeft 0; the probe refuses to report success unless that control
fires. Build both with tests/gitworkspace_browser_bundle.mjs, the old one
with ORGTREE_GIT_BUNDLE_OUT pointing at a separate directory.

No live server, no live data: ORGTREE_DATA is a fresh temp root asserted
before any orgtree import (via test_git_workspace).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend/tests"))
from test_git_workspace import Fixture, git, gw, gitsettings, store, DATA  # noqa: E402
from orgtree import gitapi  # noqa: E402
from orgtree.ledger import USER  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

assert Path(store.DATA_ROOT).resolve() == DATA.resolve()

SIDE_LANES = 4
LEFTMOST = "refs/heads/side-2"


def build_fixture() -> Fixture:
    f = Fixture()
    # four side branches, each one commit off main, so the layout has four
    # side lanes: two left of the trunk (the leftmost two columns out)
    # a branch is an ACTIVE lane only when it is checked out somewhere or
    # linked to an open ticket (gitworkspace.py), so link each one
    f.org.hire(USER, None, "haiku", 0, "agent-one")
    for i in range(SIDE_LANES):
        git(f.clone, "checkout", "-q", "-b", f"side-{i}", "main")
        f.commit(f.clone, f"side-{i}.txt", f"side {i}\n")
        item = f.org.work_create(USER, f"Left edge ticket {i}", "Fixture association so the branch is an active lane", owner="agent-one")
        store.save_org(f.org)
        gw.link_item(f.slug, f.rid, f"refs/heads/side-{i}", item["slug"])
    git(f.clone, "checkout", "-q", "main")
    gitsettings.change(lambda d: d["selected_by_org"].update({f.slug: f.rid}))
    return f


def measure(page, vp) -> dict:
    """Bounding boxes of every annotation and node relative to the viewport."""
    return page.evaluate("""() => {
      const v = document.querySelector('.git-viewport'); const vr = v.getBoundingClientRect()
      const boxes = (sel, name) => [...document.querySelectorAll(sel)].map(el => {
        const r = el.getBoundingClientRect(); return { name: el.getAttribute(name), left: r.left - vr.left, right: r.right - vr.left, width: r.width } })
      return { scrollLeft: v.scrollLeft, scrollWidth: v.scrollWidth, clientWidth: v.clientWidth,
               annotations: boxes('.git-annotation', 'data-branch'), nodes: boxes('.git-node', 'data-oid') }
    }""")


def run(out: Path, executable: str | None, old_bundle: Path) -> None:
    bundle = ROOT / "frontend/node_modules/.orgtree-git-browser"
    if not (bundle / "probe.js").is_file():
        raise RuntimeError("INERT: build Git browser fixture first (node tests/gitworkspace_browser_bundle.mjs)")
    if not (old_bundle / "probe.js").is_file():
        raise RuntimeError("INERT: --old-bundle must name a pre-fix fixture build; the control cannot run without it")
    out.mkdir(parents=True, exist_ok=True)
    f = build_fixture()
    app = FastAPI(); app.include_router(gitapi.router)
    client = TestClient(app)
    origin = "http://git-fixture.test"
    errors: list[str] = []
    config = json.dumps({"slug": f.slug, "agent": "agent-one"})
    html = (f'<!doctype html><html><head><link rel="stylesheet" href="/probe.css"></head><body><div id="root"></div>'
            f'<script>window.gitFixture={config}</script><script type="module" src="/probe.js"></script></body></html>')

    serving = {"dir": bundle}

    def route(request):
        parsed = urlsplit(request.request.url)
        if f"{parsed.scheme}://{parsed.netloc}" != origin:
            request.abort(); return
        if parsed.path == "/":
            request.fulfill(status=200, content_type="text/html", body=html)
        elif parsed.path in ("/probe.js", "/probe.css"):
            request.fulfill(status=200, content_type="text/javascript" if parsed.path.endswith(".js") else "text/css",
                            body=(serving["dir"] / parsed.path[1:]).read_bytes())
        elif parsed.path.startswith("/api/"):
            response = client.request(request.request.method, parsed.path + ("?" + parsed.query if parsed.query else ""),
                                      content=request.request.post_data or b"", headers={"content-type": "application/json"})
            request.fulfill(status=response.status_code, content_type="application/json", body=response.content)
        else:
            request.abort()

    result: dict = {"side_lanes": SIDE_LANES}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=executable)
        # the user's viewport is wide; the failure is independent of width
        # because it is about canvas coordinates below zero, not the window
        page = browser.new_page(viewport={"width": 1500, "height": 900}, device_scale_factor=1)
        page.route("**/*", route)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(origin)
        # labels are virtualised (only those near the visible window are in
        # the DOM), so wait for the trunk's node, then scroll and look
        page.locator("[data-branch='refs/heads/main']").wait_for(timeout=30000)
        assert not errors, errors
        vp = page.locator(".git-viewport")

        # scroll fully LEFT (the drag gesture is checked separately below)
        vp.evaluate("v => { v.scrollLeft = 0 }")
        page.wait_for_timeout(250)
        left = measure(page, vp)
        # the leftmost lane is side-2 (branch order: side-0 left, side-1 right,
        # side-2 left again, two columns out); its label must be rendered here
        assert any(a["name"] == LEFTMOST for a in left["annotations"]), {"rendered at scrollLeft 0": [a["name"] for a in left["annotations"]]}
        page.screenshot(path=str(out / "leftedge-fixed-scrolled-left.png"))
        result["fixed_left"] = left
        clipped = [a for a in left["annotations"] if a["left"] < 0]
        clipped_nodes = [n for n in left["nodes"] if n["left"] < 0]
        assert left["scrollLeft"] == 0
        assert not clipped, {"clipped annotations at scrollLeft 0": clipped}
        assert not clipped_nodes, {"clipped nodes at scrollLeft 0": clipped_nodes}
        leftmost = min(left["annotations"], key=lambda a: a["left"])
        assert leftmost["left"] >= 10, leftmost   # the 15px edge minus sub-pixel rounding
        assert leftmost["width"] > 200, leftmost  # the full 245px box, not a sliver

        # and fully RIGHT: nothing past the canvas
        vp.evaluate("v => { v.scrollLeft = v.scrollWidth }")
        page.wait_for_timeout(150)
        right = measure(page, vp)
        result["fixed_right"] = right
        past = [a for a in right["annotations"] if a["right"] > right["clientWidth"] + 1]
        assert not past, {"annotations past the right edge": past}

        # the drag gesture itself reaches the left edge (not only programmatic scroll)
        vp.evaluate("v => { v.scrollLeft = 400 }")
        rect = vp.bounding_box()
        x, y = rect["x"] + 400, rect["y"] + 300
        page.mouse.move(x, y); page.mouse.down(); page.mouse.move(x + 600, y, steps=10); page.mouse.up()
        dragged = measure(page, vp)
        result["dragged"] = {"scrollLeft": dragged["scrollLeft"]}
        assert dragged["scrollLeft"] == 0, dragged["scrollLeft"]
        assert min(a["left"] for a in dragged["annotations"]) >= 10

        # POSITIVE CONTROL: the pre-fix geometry must be SEEN to clip in this
        # same browser against the same fixture, or the assertions above
        # prove nothing about clipping. Same page, pre-fix bundle.
        serving["dir"] = old_bundle
        page.goto(origin)
        page.locator("[data-branch='refs/heads/main']").wait_for(timeout=30000)
        assert not errors, errors
        vp = page.locator(".git-viewport")
        vp.evaluate("v => { v.scrollLeft = 0 }")
        page.wait_for_timeout(250)
        old = measure(page, vp)
        page.screenshot(path=str(out / "leftedge-old-bundle-control.png"))
        result["old_bundle_control"] = old
        old_clipped = [a for a in old["annotations"] if a["left"] < 0]
        assert old_clipped, {"control did not clip": old["annotations"]}
        assert any(a["name"] == LEFTMOST for a in old_clipped), old_clipped
        assert min(a["left"] for a in old_clipped) < -150, old_clipped   # the reported -177 shape
        browser.close()
    result["verdict"] = "PASS"
    (out / "leftedge.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    print("PASS - leftmost label reachable at scrollLeft 0 and by drag; old-margin control clips")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--browser", help="Explicit Chromium executable when the default Playwright revision is unavailable")
    parser.add_argument("--old-bundle", type=Path, required=True, help="fixture bundle built from the pre-fix layout (the positive control)")
    args = parser.parse_args()
    run(args.out_dir, args.browser, args.old_bundle)
