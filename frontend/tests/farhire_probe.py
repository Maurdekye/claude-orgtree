"""farhire_probe.py — inspect the far-map hire expander in a real browser.

The chip rows deliberately counter-scale and therefore keep a 22px screen size
while cards shrink. A DOM test proves the component stages the dynamic list;
this probe renders the shipped CSS in Edge and proves the visual boundary that
motivates the staging is real.

    python tests/farhire_probe.py --collapsed C:/tmp/collapsed.png \
        --expanded C:/tmp/expanded.png
    python tests/farhire_probe.py --expect-fail

``--expect-fail`` seeds the expanded rows before clicking the arrow. The probe
must fail its "one visible compact control" assertion, or it is unable to see
the regression it claims to guard.
"""

import argparse
import pathlib
import sys

from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).resolve().parent
FRONTEND = HERE.parent
CSS = FRONTEND / "src" / "styles.css"
CARDS = FRONTEND / "src" / "canvas" / "cards.tsx"


def source_guard() -> str:
    cards = CARDS.read_text(encoding="utf-8")
    for needle in ("const HIRE_COMPACT_ZOOM = .75", "farCompact",
                   "hire-expand", "if (!fams.length) return null",
                   "onPointerLeave={() => setExpandedHireEdge(null)}"):
        if needle not in cards:
            raise SystemExit(f"cards.tsx is missing {needle!r}; this fixture no longer "
                             "matches the control it is supposed to inspect")
    css = CSS.read_text(encoding="utf-8")
    for needle in (".hsof.hire-compact .hire-expand",
                   ".hsof.hire-compact.is-expanded .hs-fam",
                   "@keyframes hire-families-down"):
        if needle not in css:
            raise SystemExit(f"styles.css is missing {needle!r}; update the probe")
    return css


def family(name: str, letters: str) -> str:
    return "<div class='hs-fam' data-provider='%s'>%s</div>" % (
        name, "".join(f"<button class='t-{c}'>{c[0].upper()}</button>" for c in letters.split()))


ROWS = "".join((
    family("claude", "haiku sonnet opus fable"),
    family("codex", "luna terra sol"),
    family("gemini", "flash pro"),
))


def document(css: str, open_rows: bool) -> str:
    rows = ROWS if open_rows else ""
    expanded = " is-expanded" if open_rows else ""
    return f"""<!doctype html><html><head><style>{css}
body {{ margin: 0; background: #17191c; }}
#scene {{ position: relative; width: 360px; height: 300px; overflow: hidden; }}
#world {{ position: absolute; transform: scale(.55); transform-origin: 0 0;
  --invzf: 1.81818; }}
#scene .sq {{ position: absolute; left: 270px; top: 150px; width: 124px; height: 124px; }}
/* Freeze the hover gate open so this screenshot is precisely the interactive
   hover state, not a pointer-position snapshot. */
#scene .hsof {{ opacity: 1; pointer-events: auto; }}
</style></head><body><div id='scene'><div id='world'>
  <div class='sq edge-b' id='card'>
    <div class='hsof hire-compact{expanded}' id='hire'>
      <button class='hire-expand' id='expand' type='button' aria-expanded='{str(open_rows).lower()}'>↓</button>
      {rows}
    </div>
  </div>
</div></div><script>
const rows = {ROWS!r};
document.querySelector('#expand').addEventListener('click', () => {{
  const hire = document.querySelector('#hire');
  if (hire.classList.toggle('is-expanded')) hire.insertAdjacentHTML('beforeend', rows);
  else document.querySelectorAll('#hire .hs-fam').forEach((el) => el.remove());
  document.querySelector('#expand').setAttribute('aria-expanded', hire.classList.contains('is-expanded'));
}});
</script></body></html>"""


def state(page):
    return page.evaluate("""() => ({
      card: document.querySelector('#card').getBoundingClientRect().height,
      arrows: document.querySelectorAll('.hire-expand').length,
      families: document.querySelectorAll('.hs-fam').length,
      buttons: document.querySelectorAll('.hs-fam button').length,
      expanded: document.querySelector('#hire').classList.contains('is-expanded'),
    })""")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--collapsed", type=pathlib.Path)
    parser.add_argument("--expanded", type=pathlib.Path)
    parser.add_argument("--expect-fail", action="store_true")
    args = parser.parse_args()
    css = source_guard()
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        page = browser.new_page(viewport={"width": 360, "height": 300}, device_scale_factor=2)
        page.set_content(document(css, args.expect_fail))
        before = state(page)
        if args.collapsed:
            args.collapsed.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(args.collapsed))
        errors = []
        if before["arrows"] != 1 or before["families"] or before["buttons"]:
            errors.append(f"collapsed state is not one arrow: {before}")
        if not (67 <= before["card"] <= 69):
            errors.append(f"expected a 68px card at .55 zoom, saw {before['card']:.2f}")
        page.locator("#expand").click()
        after = state(page)
        if args.expanded:
            args.expanded.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(args.expanded))
        if not after["expanded"] or after["families"] != 3 or after["buttons"] != 9:
            errors.append(f"the full max-provider list did not open: {after}")
        browser.close()
    if errors:
        print("farhire_probe: FAIL")
        for error in errors:
            print(" -", error)
        if args.expect_fail:
            print("farhire_probe: known-negative control detected")
        raise SystemExit(1)
    if args.expect_fail:
        raise SystemExit("farhire_probe: known-negative control unexpectedly passed")
    print("farhire_probe: PASS", before, "->", after)


if __name__ == "__main__":
    main()
