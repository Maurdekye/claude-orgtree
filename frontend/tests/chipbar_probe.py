"""chipbar_probe.py — can you actually click an agent's credit bar?

User report 2026-08-28: "when viewing agents from a distance, the leftward hire
coworker badges overlap the budget bar, making it untouchable unless zoomed in
far enough to reduce their relative size".

THE MECHANISM, which is the reason this has to be measured rather than reasoned
about. Both things live in the same narrow strip immediately left of the card:

    .cbar          left: -22px; width: 14px; z-index: 2     -> world x [-22, -8]
    .hsof.side-l   anchored near the card edge; z-index: 3

but they scale differently. The bar is world-scaled, so its screen width is
14*z and it collapses toward nothing as you zoom out. The chips carry
`scale(var(--invzf))`, an UNCLAMPED counter-scale, so they hold a constant
SCREEN size at every zoom. Zoom out far enough and a screen-constant column
sits on top of a bar that has shrunk to a few pixels — and because the chips
are z-index 3 against the bar's 2, and become `pointer-events: auto` the moment
the card is hovered with the left edge nearest, the chips receive the click.
The bar is not merely covered; it is unreachable.

WHY A BROWSER. jsdom does no layout: every rect there is 0x0, so an overlap
check under it passes for the same reason it would pass on a blank page. And a
unit test that multiplies constants by an assumed scale is arithmetic wearing a
measurement's clothes — a seat on this repo shipped exactly that this week,
assuming 58px for a box that really rendered at 101.63px, green throughout. So
this renders the real markup against the real src/styles.css in real headless
Edge, and asks the only question that matters:

    with the cursor ON the bar, what does document.elementFromPoint return?

That is the user's complaint stated as a measurement. Geometry overlap is
reported too, because the fix is supposed to remove the OVERLAP rather than
merely reorder what sits on top -- if you only flip z-index or pointer-events,
the hit test goes green while the badge still visually buries the bar.

    python tests/chipbar_probe.py                 # measure and check
    python tests/chipbar_probe.py --expect-fail   # KNOWN-NEGATIVE CONTROL:
                                                  # restores the PRE-FIX rules;
                                                  # must FAIL.
    python tests/chipbar_probe.py --shot out.png  # and look at it

The control is the point. "The bar is reachable at every zoom" means nothing
unless this script is demonstrably able to report that it is not.

Requires playwright with the msedge channel (same dependency, and the same
reason, as edgejump_probe.py and actlabel_probe.py next door).
"""

import argparse
import os
import pathlib
import re
import sys
import tempfile

from playwright.sync_api import sync_playwright

# the Windows console defaults to cp1252 and this script prints box-drawing and
# arrows; without this the probe dies in its own report AFTER measuring, which
# reads exactly like a measurement failure and is not one
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

HERE = pathlib.Path(__file__).resolve().parent
FRONTEND = HERE.parent
CSS = FRONTEND / "src" / "styles.css"
SHARED = FRONTEND / "src" / "canvas" / "shared.ts"
CARDS = FRONTEND / "src" / "canvas" / "cards.tsx"


def _node_size() -> tuple[int, int]:
    """Card geometry straight out of shared.ts — hard-coding 124 here would let
    the two drift in silence."""
    src = SHARED.read_text(encoding="utf-8")
    m = re.search(r"^export const NODE_W = (\d+), NODE_H = (\d+)", src, re.M)
    if not m:
        raise SystemExit(f"could not read NODE_W/NODE_H from {SHARED}")
    return int(m.group(1)), int(m.group(2))


def _zmax() -> float:
    src = SHARED.read_text(encoding="utf-8")
    m = re.search(r"^export const Z_MAX = ([\d.]+)", src, re.M)
    if not m:
        raise SystemExit(f"could not read Z_MAX from {SHARED}")
    return float(m.group(1))


NODE_W, NODE_H = _node_size()
Z_MAX = _zmax()


def _check_fixture_still_matches_source() -> None:
    """The fixture must be the markup the app really emits.

    A probe measuring markup the app never renders is worth less than no probe,
    because it is believed. These are the exact hooks the CSS under test keys
    on; if any of them moves, this file is measuring a card that does not ship.
    """
    src = CARDS.read_text(encoding="utf-8")
    for needed in ("'hsof' + (side ? ` side side-${side[0]}` : '')",
                   "'cbar' + (draftMode || drag ? ' dragging' : '')",
                   'className="hsof-bridge bridge-l"',
                   'className="hsof-bridge bridge-r"',
                   "'edge-' + edge"):
        if needed not in src:
            raise SystemExit(
                f"cards.tsx no longer emits {needed!r} — this probe's fixture "
                f"is stale and would be measuring a card the app never draws. "
                f"Update card() and this guard together.")
    css = CSS.read_text(encoding="utf-8")
    for needed in (".hsof.side-l", ".cbar {", "--invzf"):
        if needed not in css:
            raise SystemExit(
                f"styles.css no longer contains {needed!r} — the rules under "
                f"test have moved; update this probe with them.")


# Zoom levels to sweep. 0.24 is the canvas floor (OrgCanvas clamps there),
# Z_MAX the ceiling. The interesting band is the low end — that is "viewing
# agents from a distance" — but the sweep covers the range so a fix that
# merely moves the failure to a different zoom is caught rather than praised.
ZOOMS = [0.24, 0.35, 0.55, 0.8, 1.0, 2.1, 4.0, Z_MAX]

# A mid-sized holding: seat 1 + grant 5 at the default 7px/credit. Height is
# incidental to the defect (the collision is horizontal) but a realistic bar
# makes the screenshot legible and gives elementFromPoint a real target.
BAR_H = 42


def card() -> str:
    """The real card markup, trimmed to what the rules under test key on.

    `edge-l` is preset because the app sets it from pointer position: the user
    reaching for the bar IS at the left edge, so this is the state the
    complaint describes, not a contrived one.
    """
    chips = "".join(
        f"<button class='t-{t}'>{ltr}</button>"
        for t, ltr in (("haiku", "H"), ("sonnet", "S"),
                       ("opus", "O"), ("fable", "F")))
    return (
        f"<div class='sq live norm tier-haiku edge-l' "
        f"style='width:{NODE_W}px;height:{NODE_H}px'>"
        f"<div class='cbar' style='height:{BAR_H}px'>"
        f"<div class='cbar-clip'></div></div>"
        f"<div class='hsof-bridge bridge-l'></div>"
        f"<div class='hsof-bridge bridge-r'></div>"
        f"<div class='hsof side side-l'>{chips}</div>"
        f"<div class='hsof side side-r'>{chips}</div>"
        f"<div class='doc-chips'><div class='doc-chip'>D</div></div>"
        f"<div class='sq-head'><span class='name'>ceo</span></div>"
        f"</div>")


def build_page(css: str, z: float) -> str:
    """One card at one zoom, inside a .space carrying exactly the custom
    properties OrgCanvas sets on it — same expressions, so the probe cannot
    disagree with the app about what --invzf is."""
    invz = min(2.4, max(1 / Z_MAX, 1 / z))
    invzf = max(1 / Z_MAX, 1 / z)
    return (
        "<!doctype html><html><head><meta charset='utf-8'><style>\n"
        + css +
        "\n/* probe chrome only: never touches the rules under test */"
        "\n.probe-pad{padding:260px;background:#1f1f1f}"
        "\n</style></head><body style='margin:0;background:#1f1f1f'>"
        "<div class='probe-pad'>"
        f"<div class='space' style=\"transform:scale({z});"
        f"transform-origin:top left;"
        f"--invz:{invz:.3f};--invzf:{invzf:.3f};--z:{z:.3f}\">"
        + card() +
        "</div></div></body></html>")


# Rects for the three actors, plus the hit test. Every number is read from the
# browser's own layout; nothing here is derived from a constant.
MEASURE = """
() => {
  const r = (el) => { if (!el) return null; const b = el.getBoundingClientRect();
    return {x: b.x, y: b.y, w: b.width, h: b.height, right: b.right, bottom: b.bottom}; };
  const sq = document.querySelector('.sq');
  const bar = document.querySelector('.cbar');
  const chipsL = document.querySelector('.hsof.side-l');
  const chipsR = document.querySelector('.hsof.side-r');
  const docs = document.querySelector('.doc-chips');
  const cs = getComputedStyle(chipsL);
  return {
    sq: r(sq), bar: r(bar), chipsL: r(chipsL), chipsR: r(chipsR), docs: r(docs),
    chipsLPointer: cs.pointerEvents, chipsLOpacity: cs.opacity,
  };
}
"""

# What is actually under the cursor, at the point the user is aiming for.
HIT = """
(pt) => {
  const el = document.elementFromPoint(pt.x, pt.y);
  if (!el) return {tag: null, cls: null};
  const own = el.closest('.cbar, .hsof, .doc-chips, .sq');
  return {tag: el.tagName.toLowerCase(), cls: el.className || null,
          owner: own ? own.className : null};
}
"""


def overlap(a, b) -> float:
    """Horizontal overlap in CSS px. Horizontal because the collision is
    horizontal: both actors run the full height of the strip beside the card,
    and a fix is only real if it separates them on x."""
    if not a or not b:
        return 0.0
    return max(0.0, min(a["right"], b["right"]) - max(a["x"], b["x"]))


# Restores the PRE-FIX geometry for --expect-fail. Appended last so it wins on
# specificity-tie/order. This is the known-negative control: with these rules
# the probe MUST report the bar unreachable, or it is not measuring anything.
PRE_FIX_CSS = """
/* ---- probe control: pre-fix rules, restored verbatim ---- */
.hsof.side-l { left: -21px; right: auto; padding-right: 0;
  transform: translateY(-50%) scale(var(--invzf, 1));
  transform-origin: center right; }
.hsof.side-r { right: -21px; left: auto; padding-left: 0;
  transform: translateY(-50%) scale(var(--invzf, 1));
  transform-origin: center left; }
"""


def run(css_text: str, shot: str | None = None, verbose: bool = True):
    rows = []
    fd, page = tempfile.mkstemp(suffix=".html",
                                dir=str(FRONTEND / "node_modules"))
    os.close(fd)
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(channel="msedge", headless=True)
            pg = b.new_page(viewport={"width": 1600, "height": 900},
                            device_scale_factor=2)
            for z in ZOOMS:
                pathlib.Path(page).write_text(build_page(css_text, z),
                                              encoding="utf-8")
                pg.goto(pathlib.Path(page).as_uri())
                pg.wait_for_selector(".cbar", timeout=8000)
                m = pg.evaluate(MEASURE)
                if not m["bar"] or not m["chipsL"]:
                    raise SystemExit(
                        "the fixture did not render a bar and a chip column — "
                        "nothing below would mean anything")
                # put the real cursor on the bar, exactly where the user aims,
                # THEN ask what is under it. Hovering matters: the chips are
                # gated on .sq:hover, so a hit test without a real pointer
                # would measure a card in a state the user never sees.
                # ⚠ clamp the aim point into the VIEWPORT before hit-testing.
                # At desk zoom the bar is taller than the window, so its
                # geometric centre is off-screen and elementFromPoint returns
                # null — which this probe cheerfully reported as "the bar is
                # unreachable". That is a false alarm of exactly the kind that
                # trains people to stop reading a check, so it is measured
                # properly instead: aim at the centre of the bar's VISIBLE
                # part, and say plainly when none of it is visible.
                vw, vh = 1600, 900
                vis_x0, vis_x1 = max(0.0, m["bar"]["x"]), min(float(vw), m["bar"]["right"])
                vis_y0, vis_y1 = max(0.0, m["bar"]["y"]), min(float(vh), m["bar"]["bottom"])
                if vis_x1 <= vis_x0 or vis_y1 <= vis_y0:
                    rows.append({"z": z, "offscreen": True, "bar": m["bar"],
                                 "chipsL": m["chipsL"], "chipsR": m["chipsR"],
                                 "docs": m["docs"]})
                    continue
                bx = (vis_x0 + vis_x1) / 2
                by = (vis_y0 + vis_y1) / 2
                pg.mouse.move(bx, by)
                pg.wait_for_timeout(60)      # let the opacity transition settle
                m2 = pg.evaluate(MEASURE)    # re-read: hover changes the chips
                hit = pg.evaluate(HIT, {"x": bx, "y": by})
                if shot and abs(z - 0.35) < 1e-9:
                    pathlib.Path(shot).parent.mkdir(parents=True, exist_ok=True)
                    pg.locator(".probe-pad").screenshot(path=shot)
                # TRANSIT: walk the cursor from just inside the card's left
                # edge out to the chips, and watch whether the chips stay
                # interactive the whole way. They are gated on `.sq:hover`, so
                # any x where the pointer is over neither the card nor one of
                # its descendants makes them vanish mid-reach. Moving the chips
                # clear of the bar is only a real fix if it does not open such
                # a gap — otherwise the bar becomes clickable and the hire
                # gesture becomes unreachable, which is trading one bug for
                # another rather than fixing one.
                # ⚠ DIRECTION IS LOAD-BEARING: walk OUTWARD from inside the
                # card, because that is the only way a user can arrive. The
                # chips are `pointer-events: none` until the card is hovered,
                # so approaching from open canvas they are not hit-testable at
                # all and never can be — walking inward measures a path nobody
                # can take and reports a working fix as broken. (It did.)
                cy = m2["chipsL"]["y"] + m2["chipsL"]["h"] / 2
                x_from, x_to = m2["sq"]["x"] + 4, m2["chipsL"]["x"] + 2
                dead, run_, worst = [], 0.0, 0.0
                if 0 <= cy <= 900:
                    step = 1.0
                    x = x_from
                    while x >= x_to:
                        if 0 <= x <= 1600:
                            pg.mouse.move(x, cy)
                            live = pg.evaluate(
                                "() => getComputedStyle(document.querySelector"
                                "('.hsof.side-l')).pointerEvents")
                            if live != "auto":
                                run_ += step
                                worst = max(worst, run_)
                                dead.append(round(x, 1))
                            else:
                                run_ = 0.0
                        x -= step
                rows.append({
                    "z": z, "bar": m2["bar"], "chipsL": m2["chipsL"],
                    "chipsR": m2["chipsR"], "docs": m2["docs"],
                    "hit": hit, "pointer": m2["chipsLPointer"],
                    "opacity": m2["chipsLOpacity"], "deadrun": worst,
                })
            b.close()
    finally:
        os.unlink(page)

    fails = []
    if verbose:
        print(f"  card {NODE_W}x{NODE_H}, bar {BAR_H}px tall, "
              f"cursor placed on the bar's centre at each zoom\n")
        print("     zoom   bar x-span        chips-L x-span     overlap  "
              "what the click hits")
        print("  " + "-" * 76)
    for r in rows:
        if r.get("offscreen"):
            if verbose:
                print(f"     {r['z']:5.2f}   bar is larger than the window at "
                      f"this zoom — no visible part to aim at; not measured")
            continue
        bar, ch = r["bar"], r["chipsL"]
        ov = overlap(bar, ch)
        owner = (r["hit"].get("owner") or "").strip()
        reaches_bar = "cbar" in owner
        if verbose:
            flag = "  " if (ov == 0 and reaches_bar) else "!!"
            print(f"  {flag}{r['z']:5.2f}   "
                  f"[{bar['x']:8.1f},{bar['right']:8.1f}]  "
                  f"[{ch['x']:8.1f},{ch['right']:8.1f}]  "
                  f"{ov:7.1f}  {owner or '(nothing)'}")
        if not reaches_bar:
            fails.append(
                f"z={r['z']}: the cursor is on the bar and the click lands on "
                f"{owner or 'nothing'} — the bar is UNREACHABLE. This is the "
                f"user's complaint.")
        if ov > 0:
            fails.append(
                f"z={r['z']}: the left chips overlap the bar by {ov:.1f}px. "
                f"Even if the hit test passes, the badge is sitting on top of "
                f"the bar — the fix must remove the overlap, not reorder it.")

    # reaching the chips must not have become the new problem
    if verbose:
        print("\n  transit — cursor walked from the card out to the chips;"
              "\n  a dead run is where the chips stop being interactive mid-reach")
    for r in rows:
        if r.get("offscreen"):
            continue
        if verbose:
            print(f"    z={r['z']:5.2f}  worst dead run {r['deadrun']:6.1f}px")
        if r["deadrun"] > 0:
            fails.append(
                f"z={r['z']}: reaching the chips crosses {r['deadrun']:.1f}px "
                f"where they are not interactive — they vanish mid-reach. The "
                f"bar may be clickable now, but the hire gesture is not.")

    # the symmetric case: right chips vs the doc chips, which are world-scaled
    # exactly like the bar and sit in the mirror strip
    if verbose:
        print("\n  symmetric strip (right side): chips-R vs .doc-chips")
    for r in rows:
        ov = overlap(r["chipsR"], r["docs"])
        if verbose and r["docs"]:
            d, c = r["docs"], r["chipsR"]
            print(f"    z={r['z']:5.2f}  docs [{d['x']:7.1f},{d['right']:7.1f}]"
                  f"  chips-R [{c['x']:7.1f},{c['right']:7.1f}]"
                  f"  overlap {ov:7.1f}px")
        if ov > 0:
            fails.append(
                f"z={r['z']}: SYMMETRIC — the right chips overlap the doc "
                f"chips by {ov:.1f}px (same mechanism, mirrored).")
    return fails


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--expect-fail", action="store_true",
                    help="restore the pre-fix rules; the run MUST fail")
    ap.add_argument("--shot", help="write a screenshot at z=0.35")
    a = ap.parse_args()

    _check_fixture_still_matches_source()
    css = CSS.read_text(encoding="utf-8")
    if a.expect_fail:
        css = css + PRE_FIX_CSS
        print("chipbar_probe: KNOWN-NEGATIVE CONTROL (pre-fix rules restored)")
    else:
        print("chipbar_probe: measuring the shipped rules")
    fails = run(css, shot=a.shot)

    if a.expect_fail:
        if fails:
            print(f"\n  CONTROL OK — the pre-fix sheet fails, as it must "
                  f"({len(fails)} finding(s)). The probe can see this defect.")
            print("   e.g. " + fails[0])
            return 0
        print("\n  CONTROL FAILED: the pre-fix rules measured CLEAN.")
        print("  This probe cannot see the defect it exists to catch, so a "
              "green run against the real sheet proves nothing.")
        return 1

    if fails:
        print(f"\n  {len(fails)} finding(s):")
        for f in fails:
            print("   - " + f)
        return 1
    print("\n  OK — at every zoom the cursor on the bar hits the bar, and the "
          "chips do not overlap it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
