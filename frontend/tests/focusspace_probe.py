"""focusspace_probe.py — w14aace89 in a REAL browser: does the switchboard's
actual painted box, and a focused agent desk's, fit inside the free rectangle
the pins leave?

WHY THIS EXISTS. `focusspace.test.tsx` runs under jsdom, which does no layout.
It can only read back what the component WRITES — the `.space` transform and
the eye's inline width — so it can show that the camera aims at the region and
that `eyeW` follows the region's aspect. It cannot show that the surface
actually FITS, and there are two concrete reasons it might not:

  * `focusView` floors the zoom at Z_DESK (2.1) on purpose, so a region too
    small for a readable desk gets a readable desk that OVERFLOWS it.
  * the eye's width is `Math.max(eyeW, USER_W)`, so a region narrower than the
    plain square cannot be honoured by aspect alone.

Matching aspect is therefore NOT the same claim as fitting, and the review
asked for the fitting one. Only a browser can answer it.

WHAT IS MEASURED. Real `getBoundingClientRect()` of `.eye-desk` (the
switchboard) and `.sq.desk:not(.user)` (a focused agent desk), against the
free region computed from the same pin rectangles. Overflow is reported per
edge, in pixels, so a failure says WHERE it escaped rather than just that it
did.

⚠ ANTI-VACUITY — A RED BASELINE, NOT AN INSPECTION. `--mutant viewport-centre`
rebuilds the SAME page from an OrgCanvas whose `focusView` centres on the
whole viewport again (the pre-w14aace89 camera), and `--mutant no-eye-region`
restores the viewport-aspect `eyeW`. The suite must FAIL against those and
pass against the real one. A probe that has only ever seen the fixed build is
evidence about the probe.

    cd frontend
    python tests/focusspace_probe.py               # green: the shipped canvas
    python tests/focusspace_probe.py --mutant viewport-centre   # must FAIL
    python tests/focusspace_probe.py --list-mutants

Requires playwright with the msedge channel (same dependency as the other
browser probes here). No backend, no live provider: the page is a local file.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
FRONTEND = HERE.parent
BUILD = HERE / "focusspace_build.mjs"

VP_W, VP_H = 1280, 900
GAP = 12          # clearRect.PIN_GAP
PIN_MIN_W, PIN_MIN_H = 320, 240   # pins.tsx sizeFloor — fixtures respect it

#: Each mutant is (old, new, why it must go red). These are exact-match
#: substitutions applied to OrgCanvas.tsx inside the bundler.
MUTANTS: dict[str, tuple[str, str, str]] = {
    "viewport-centre": (
        "      x: r.x + r.w / 2 - (p.x + NODE_W / 2) * zz,\n"
        "      y: r.y + r.h / 2 - (p.y + NODE_H / 2) * zz,",
        "      x: vp.width / 2 - (p.x + NODE_W / 2) * zz,\n"
        "      y: vp.height / 2 - (p.y + NODE_H / 2) * zz,",
        "the camera centres on the whole viewport again — the desk lands "
        "under the pin"),
    "eye-height-only": (
        "      ? Math.min(Z_MAX, (r.h - 48) / USER_H, (r.w - 48) / eyeWorldW(r))",
        "      ? Math.min(Z_MAX, (r.h - 48) / USER_H)",
        "the eye fits on HEIGHT alone again — in a tall/narrow region the "
        "USER_W floor makes the cell relatively wider than the region is, so "
        "the switchboard overflows sideways (the §D defect this probe found)"),
    "no-region-gate": (
        "      const fillR = vp ? regionOf(vp).rect : null",
        "      const fillR = vp ? { w: vp.width, h: vp.height } : null",
        "the eye-focus gate measures screen-filling against the whole "
        "viewport again — the switchboard cannot open with a pin up"),
}


def build(outdir: pathlib.Path, mutant: str | None) -> None:
    args = [str(BUILD), str(outdir)]
    tmp = None
    if mutant:
        old, new, _ = MUTANTS[mutant]
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                          encoding="utf-8")
        json.dump([{"old": old, "new": new}], tmp)
        tmp.close()
        args += ["--subst", tmp.name]
    try:
        subprocess.run(["node", *args], check=True, cwd=str(FRONTEND))
    finally:
        if tmp:
            pathlib.Path(tmp.name).unlink(missing_ok=True)
    if not (outdir / "probe.css").exists():
        raise SystemExit("bundle produced no probe.css — styles.css import lost")


# ---------------------------------------------------------------- geometry
def obstacle(pin: dict, vp: dict) -> dict | None:
    """clip to the viewport, THEN grow by the gap — clearRect.obstacleOf"""
    x0, y0 = max(pin["x"], 0), max(pin["y"], 0)
    x1 = min(pin["x"] + pin["w"], vp["w"])
    y1 = min(pin["y"] + pin["h"], vp["h"])
    if x1 <= x0 or y1 <= y0:
        return None
    return {"x": max(0, x0 - GAP), "y": max(0, y0 - GAP),
            "r": min(vp["w"], x1 + GAP), "b": min(vp["h"], y1 + GAP)}


def clear_region(pins: list[dict], vp: dict) -> dict | None:
    """The expected free region, computed INDEPENDENTLY of the TypeScript so a
    shared bug cannot agree with itself. Largest by area; ties to the nearest
    viewport centre, then smaller x, then smaller y."""
    obs = [o for o in (obstacle(p, vp) for p in pins) if o]
    if not obs:
        return {"x": 0, "y": 0, "w": vp["w"], "h": vp["h"]}
    xs = sorted({0, vp["w"]} | {o["x"] for o in obs} | {o["r"] for o in obs})
    ys = sorted({0, vp["h"]} | {o["y"] for o in obs} | {o["b"] for o in obs})
    xs = [v for v in xs if 0 <= v <= vp["w"]]
    ys = [v for v in ys if 0 <= v <= vp["h"]]
    best = None
    for i, x0 in enumerate(xs):
        for x1 in xs[i + 1:]:
            for j, y0 in enumerate(ys):
                for y1 in ys[j + 1:]:
                    if any(x0 < o["r"] and o["x"] < x1
                           and y0 < o["b"] and o["y"] < y1 for o in obs):
                        continue
                    w, h = x1 - x0, y1 - y0
                    drift = ((x0 + w / 2) - vp["w"] / 2) ** 2 \
                        + ((y0 + h / 2) - vp["h"] / 2) ** 2
                    key = (-(w * h), drift, x0, y0)
                    if best is None or key < best[0]:
                        best = (key, {"x": x0, "y": y0, "w": w, "h": h})
    return best[1] if best else None


def overflow(box: dict, region: dict, tol: float = 1.0) -> dict:
    """how far `box` escapes `region` on each edge, in real px"""
    return {
        "left": max(0.0, region["x"] - box["x"] - tol),
        "top": max(0.0, region["y"] - box["y"] - tol),
        "right": max(0.0, box["x"] + box["width"] - (region["x"] + region["w"]) - tol),
        "bottom": max(0.0, box["y"] + box["height"] - (region["y"] + region["h"]) - tol),
    }


BOX = """
(sel) => {
  const el = document.querySelector(sel);
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return { x: r.x, y: r.y, width: r.width, height: r.height };
}
"""


class Page:
    def __init__(self, pg, html: pathlib.Path):
        self.pg = pg
        self.html = html

    def open(self) -> None:
        self.pg.goto(self.html.as_uri(), wait_until="load")
        self.pg.wait_for_selector(".viewport", state="attached", timeout=8000)
        self.pg.wait_for_timeout(400)   # let the opening glide settle

    def reset(self, pins: list[dict] | None = None) -> None:
        """⚠ EVERY SCENARIO STARTS FROM A FRESH PAGE. Focusing a card turns it
        into a desk, and its `.name` element goes away — so a second
        `click_agent` for the same agent silently finds nothing, does nothing,
        and leaves the PREVIOUS scenario's camera in place. That is exactly how
        the first cut of this probe "measured" a viewport-centred camera and
        reported a bug that was not there. A reload also drops any focus, any
        open desk and any leftover pin, so each section's numbers belong to it
        alone."""
        self.open()
        self.clear_pins()
        if pins:
            self.set_pins(pins)
        self.pg.wait_for_timeout(200)

    def set_pins(self, pins: list[dict]) -> None:
        self.pg.evaluate("(ps) => window.__probe.setPins(ps)", pins)
        self.pg.wait_for_timeout(150)

    def clear_pins(self) -> None:
        self.pg.evaluate("() => window.__probe.clearPins()")
        self.pg.wait_for_timeout(150)

    def click_eye(self) -> bool:
        """Open the switchboard via the HUD eye button.

        ⚠ DISPATCHED DIRECTLY, NOT AS A HIT-TESTED MOUSE CLICK, and that is a
        deliberate concession rather than a shortcut. `.hud-eye` sits at the
        bottom-left of the viewport (measured: 30x28 at 11,765), so a pinned
        window occupying the lower band genuinely COVERS it —
        `document.elementFromPoint` at its centre returns the pin's content.
        That is correct UI behaviour, not a defect: a pin is supposed to be on
        top. But it means a real mouse click lands on the pin and the
        switchboard never opens, which the first cut of this probe reported as
        "the eye-focus gate is broken" when nothing was broken. Bypassing hit
        testing keeps the subject of the measurement the switchboard's
        GEOMETRY, which is what this probe is for. Reaching the button around
        a covering pin is a separate question and is not claimed here."""
        n = self.pg.evaluate(
            "() => { const e = document.querySelector('.hud-eye');"
            " if (!e) return 0; e.click(); return 1 }")
        if not n:
            return False
        self.pg.wait_for_timeout(1200)
        return True

    def click(self, sel: str) -> bool:
        # ⚠ NOT `.sq.user`. The eye card lives at world x=6000 and is normally
        # panned off-screen, so playwright refuses to click it ("outside of the
        # viewport"). `.hud-eye` is the button the UI itself offers for
        # "jump to the switchboard", and it calls the same camera path.
        loc = self.pg.locator(sel).first
        if loc.count() == 0:
            return False
        loc.click(force=True)
        self.pg.wait_for_timeout(900)   # the glide, and the desk it opens
        return True

    def click_agent(self, name: str) -> bool:
        js = """
        (name) => {
          const c = [...document.querySelectorAll('.sq')].find(
            (e) => e.querySelector('.name')?.textContent?.trim() === name);
          if (!c) return false;
          const r = c.getBoundingClientRect();
          return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
        }
        """
        at = self.pg.evaluate(js, name)
        if not at:
            return False
        self.pg.mouse.click(at["x"], at["y"])
        self.pg.wait_for_timeout(900)
        return True

    def box(self, sel: str) -> dict | None:
        return self.pg.evaluate(BOX, sel)

    def viewport_box(self) -> dict:
        b = self.box(".viewport")
        assert b, "no .viewport"
        return b


def run(html: pathlib.Path, verbose: bool = True) -> tuple[list[str], dict]:
    fails: list[str] = []
    obs: dict = {}
    errors: list[str] = []

    def bad(msg: str) -> None:
        fails.append(msg)
        if verbose:
            print(f"  FAIL  {msg}")

    def ok(msg: str) -> None:
        if verbose:
            print(f"  ok    {msg}")

    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge", headless=True)
        ctx = b.new_context(viewport={"width": VP_W, "height": VP_H})
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        P = Page(pg, html)
        P.open()

        vpbox = P.viewport_box()
        vp = {"w": vpbox["width"], "h": vpbox["height"]}
        obs["viewport"] = vp
        if vp["w"] < 400 or vp["h"] < 400:
            bad(f"the canvas viewport measured {vp} — the page did not lay out, "
                "so nothing below would mean anything")
            return fails, obs

        # ---- §A the rig can see a real desk box at all (positive control)
        P.reset()
        if not P.click_agent("cto"):
            bad("§A no card for cto — the fixture did not render")
            return fails, obs
        bare_desk = P.box(".sq.desk:not(.user)")
        obs["bare_desk"] = bare_desk
        if not bare_desk or bare_desk["width"] < 50:
            bad(f"§A the focused agent desk has no real box ({bare_desk}) — "
                "every fit assertion below would be vacuous")
            return fails, obs
        ok(f"§A a focused agent desk paints a real box: "
           f"{bare_desk['width']:.0f}x{bare_desk['height']:.0f}")

        # ---- §B an ASYMMETRICAL clear zone: agent desk inside the region
        # a tall pin down the left AND a short one across the bottom-right
        pins_asym = [
            {"id": "qa", "x": 0, "y": 0, "w": 360, "h": vp["h"]},
            {"id": "ops", "x": vp["w"] - 420, "y": vp["h"] - 300, "w": 420, "h": 300},
        ]
        P.reset(pins_asym)
        P.click_agent("cto")
        reg = clear_region(pins_asym, vp)
        obs["asym_region"] = reg
        desk = P.box(".sq.desk:not(.user)")
        obs["asym_desk"] = desk
        if not reg:
            bad("§B no free region computed for the asymmetrical fixture")
        elif not desk:
            bad("§B the agent desk did not open in an asymmetrical clear zone — "
                "the nearest-card search may still be measuring from the "
                "viewport centre")
        else:
            ov = overflow(desk, reg)
            obs["asym_desk_overflow"] = ov
            centre = desk["x"] + desk["width"] / 2
            rcentre = reg["x"] + reg["w"] / 2
            if abs(centre - rcentre) > 12:
                bad(f"§B the desk centre is at x={centre:.0f} but the free "
                    f"region's centre is x={rcentre:.0f}")
            else:
                ok(f"§B the agent desk centres in the asymmetrical free region "
                   f"(x={centre:.0f} vs {rcentre:.0f})")
            if any(v > 0 for v in ov.values()):
                bad(f"§B the agent desk overflows the free region by {ov} px")
            else:
                ok("§B …and its painted box fits inside it")

        # ---- §C the SWITCHBOARD's real box, short/wide region
        # a bottom band leaves a WIDE, SHORT region
        pins_wide = [{"id": "qa", "x": 0, "y": vp["h"] - 340, "w": vp["w"], "h": 340}]
        P.reset(pins_wide)
        P.click_eye()
        reg_w = clear_region(pins_wide, vp)
        sw = P.box(".eye-desk")
        obs["wide_region"], obs["wide_switchboard"] = reg_w, sw
        if not sw:
            bad("§C the switchboard did not open in a wide/short free region — "
                "the eye-focus gate must measure screen-filling against the "
                "region, not the viewport")
        elif reg_w:
            ov = overflow(sw, reg_w)
            obs["wide_overflow"] = ov
            if any(v > 0 for v in ov.values()):
                bad(f"§C the switchboard's painted box overflows the free "
                    f"region by {ov} px (region {reg_w}, box "
                    f"{sw['width']:.0f}x{sw['height']:.0f} at "
                    f"{sw['x']:.0f},{sw['y']:.0f})")
            else:
                ok(f"§C the switchboard fits a wide/short region "
                   f"({sw['width']:.0f}x{sw['height']:.0f} inside "
                   f"{reg_w['w']}x{reg_w['h']})")

        # ---- §D the SWITCHBOARD's real box, tall/narrow region
        pins_tall = [{"id": "qa", "x": vp["w"] - 520, "y": 0, "w": 520, "h": vp["h"]}]
        P.reset(pins_tall)
        P.click_eye()
        reg_t = clear_region(pins_tall, vp)
        sw2 = P.box(".eye-desk")
        obs["tall_region"], obs["tall_switchboard"] = reg_t, sw2
        if not sw2:
            bad("§D the switchboard did not open in a tall/narrow free region")
        elif reg_t:
            ov = overflow(sw2, reg_t)
            obs["tall_overflow"] = ov
            if any(v > 0 for v in ov.values()):
                bad(f"§D the switchboard's painted box overflows the free "
                    f"region by {ov} px (region {reg_t}, box "
                    f"{sw2['width']:.0f}x{sw2['height']:.0f} at "
                    f"{sw2['x']:.0f},{sw2['y']:.0f})")
            else:
                ok(f"§D the switchboard fits a tall/narrow region "
                   f"({sw2['width']:.0f}x{sw2['height']:.0f} inside "
                   f"{reg_t['w']}x{reg_t['h']})")

        # ---- §E no pins: the region is the viewport and nothing is cramped
        P.reset()
        P.click_eye()
        sw3 = P.box(".eye-desk")
        obs["bare_switchboard"] = sw3
        if not sw3:
            bad("§E the switchboard did not open with NO pins at all — the "
                "unpinned path must be untouched")
        else:
            ov = overflow(sw3, {"x": 0, "y": 0, "w": vp["w"], "h": vp["h"]})
            if any(v > 0 for v in ov.values()):
                bad(f"§E unpinned, the switchboard escapes the viewport by {ov}")
            else:
                ok("§E unpinned, the switchboard fits the whole viewport")

        ctx.close()
        b.close()

    # the fixture is a local file with no backend, so the app's own /api fetches
    # fail by design. Those are NOT defects and must not drown a real one; any
    # OTHER page error still counts.
    real = [e for e in errors if "CORS" not in e and "Failed to load resource" not in e
            and "/api/" not in e and "Failed to fetch" not in e]
    if real:
        obs["page_errors"] = real[:10]
        bad(f"the page logged {len(real)} unexpected error(s): {real[:3]}")
    return fails, obs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mutant", choices=sorted(MUTANTS))
    ap.add_argument("--list-mutants", action="store_true")
    ap.add_argument("--json")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()

    if a.list_mutants:
        for k, (_, _, why) in sorted(MUTANTS.items()):
            print(f"{k:20s} {why}")
        return 0

    out = pathlib.Path(tempfile.mkdtemp(prefix="focusspace-probe-"))
    build(out, a.mutant)
    html = out / "probe.html"
    if a.mutant:
        print(f"RED BASELINE — mutant {a.mutant!r}: {MUTANTS[a.mutant][2]}")
    fails, obs = run(html)
    report = {"mutant": a.mutant, "fails": fails, "observed": obs}
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(report, indent=2), encoding="utf-8")
    if not a.keep:
        import shutil
        shutil.rmtree(out, ignore_errors=True)

    print()
    if a.mutant:
        # a mutant MUST break something, or the probe proves nothing
        if fails:
            print(f"EXPECTED RED: {len(fails)} failure(s) against mutant "
                  f"{a.mutant!r} — the probe can see this break.")
            return 0
        print(f"⚠ MUTANT {a.mutant!r} PASSED — this probe does NOT actually "
              "test what it claims to.")
        return 1
    if fails:
        print(f"{len(fails)} FAILURE(S)")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
