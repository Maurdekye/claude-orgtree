"""picker_probe.py — the OpenRouter model-selection modal, rendered and measured.

User ask 2026-09-03: the modal shows EVERY selected model on the modal itself
(overview at a glance) and each entry deselects directly (no searching for
the model first). The list is a wrapping row of chips — monogram card, label,
a ✕ icon button — wearing the selected row's tint. Behaviour (select →
appears → deselect → disappears from the list AND the search row) is proved
by `openrouter.test.tsx §2b` under jsdom; LAYOUT is not something jsdom can
see, so this probe renders the real component over styles.css in Edge and
measures it, with six favorites so the list wraps and one label is long.

    python -B tests/picker_probe.py [--shot PNG]
    python -B tests/picker_probe.py --expect-fail      (the control)

The KNOWN-NEGATIVE CONTROL shrinks the ✕ to 10px and lets the label run
unclipped; the probe must FAIL it, or a green run proves only that the probe
can print OK.

WHAT IT CHECKS
  1. the list is on the dialog, above the search box, with one chip per
     favorite, and every chip sits INSIDE the dialog's box (no overflow)
  2. every ✕ is a real target (≥ 20×20 css px), inside its chip, icon-only
  3. a long label is clipped inside its chip, not spilling over the ✕
  4. the list wears the selected row's tint: its border colour equals the
     border colour of a selected search row (same meaning, same colour)
  5. the search rows of the favorites read selected (the fixture's state)
"""
from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys
import tempfile

from playwright.sync_api import sync_playwright

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = pathlib.Path(__file__).resolve().parent
FRONTEND = HERE.parent
CSS = FRONTEND / "src" / "styles.css"

MUTANT = """
.orr-sel-x { width: 10px !important; height: 10px !important; }
.orr-sel-name { max-width: none !important; overflow: visible !important; }
.orr-sel { max-width: 12em !important; }
"""

FRAME = """
body { margin: 0; background: #1f1f1f; color: #e8e8e8; font: 13px system-ui, sans-serif; }
"""

MEASURE = r"""() => {
  const bad = []
  const inside = (a, b, slack = 0.5) =>
    a.left >= b.left - slack && a.right <= b.right + slack && a.top >= b.top - slack && a.bottom <= b.bottom + slack
  const dialog = document.querySelector('[role="dialog"].orr-picker')
  if (!dialog) return ['no dialog rendered']
  const box = dialog.getBoundingClientRect()
  const list = dialog.querySelector('.orr-selected')
  if (!list) return ['no selected list on the dialog']
  const search = dialog.querySelector('.orr-search')
  if (!(list.getBoundingClientRect().bottom <= search.getBoundingClientRect().top + 0.5))
    bad.push('the selected list is not above the search box')
  const chips = [...list.querySelectorAll('.orr-sel')]
  if (chips.length !== 6) bad.push(`expected 6 chips, found ${chips.length}`)
  const rows = new Set(chips.map((c) => Math.round(c.getBoundingClientRect().top)))
  if (rows.size < 2) bad.push('six chips did not wrap onto a second line at 680px')
  for (const chip of chips) {
    const r = chip.getBoundingClientRect()
    const name = chip.querySelector('.orr-sel-name')?.textContent ?? '?'
    if (!inside(r, box)) bad.push(`chip "${name}" overflows the dialog`)
    const x = chip.querySelector('.orr-sel-x')
    const xr = x.getBoundingClientRect()
    if (!(xr.width >= 20 && xr.height >= 20)) bad.push(`✕ on "${name}" is ${xr.width.toFixed(0)}×${xr.height.toFixed(0)} < 20×20`)
    if (!inside(xr, r)) bad.push(`✕ on "${name}" sits outside its chip`)
    if (x.textContent.trim() !== '' || !x.querySelector('svg')) bad.push(`✕ on "${name}" is not icon-only`)
    const lab = chip.querySelector('.orr-sel-name')
    const lr = lab.getBoundingClientRect()
    if (!(lr.right <= xr.left + 0.5)) bad.push(`label "${name}" runs into the ✕`)
    if (lab.scrollWidth > lab.clientWidth + 1 && getComputedStyle(lab).textOverflow !== 'ellipsis')
      bad.push(`long label "${name}" overflows without an ellipsis`)
  }
  const long = chips.find((c) => c.querySelector('.orr-sel-name')?.textContent?.includes('maverick'))
  if (long) {
    const lab = long.querySelector('.orr-sel-name')
    if (!(lab.scrollWidth > lab.clientWidth + 1)) bad.push('the long label was not clipped')
  }
  const on = [...dialog.querySelectorAll('.orr-row.on')]
  if (on.length !== 6) bad.push(`expected the 6 favorites' rows to read selected, found ${on.length}`)
  if (on.length) {
    const a = getComputedStyle(list).borderTopColor, b = getComputedStyle(on[0]).borderTopColor
    if (a !== b) bad.push(`the list's border (${a}) is not the selected row's (${b})`)
  }
  return bad
}"""


def dump() -> str:
    with tempfile.TemporaryDirectory(prefix="orgtree-picker-") as tmp:
        out = pathlib.Path(tmp) / "picker.html"
        subprocess.run(["node", str(HERE / "picker_dump.mjs"), str(out)],
                       cwd=FRONTEND, check=True, capture_output=True)
        return out.read_text(encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--expect-fail", action="store_true")
    ap.add_argument("--shot")
    args = ap.parse_args()
    fragment = dump()
    css = CSS.read_text(encoding="utf-8")
    html = (f"<!doctype html><meta charset='utf-8'><style>{css}\n{FRAME}\n"
            f"{MUTANT if args.expect_fail else ''}</style>\n{fragment}")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 900, "height": 820},
                                device_scale_factor=2)
        page.set_content(html)
        page.wait_for_timeout(150)
        bad = page.evaluate(MEASURE)
        if args.shot:
            page.screenshot(path=args.shot, full_page=True)
            print(f"saved {args.shot}")
        browser.close()
    if args.expect_fail:
        if not bad:
            print("CONTROL FAILED — the broken chips passed the probe")
            return 1
        print("CONTROL OK — the broken chips are caught: " + "; ".join(bad[:3])
              + (f" … (+{len(bad) - 3})" if len(bad) > 3 else ""))
        return 0
    if bad:
        print("\n".join("FAIL: " + b for b in bad))
        return 1
    print("OK — six chips on the dialog above the search box, wrapping, inside the box; "
          "every ✕ ≥ 20×20 and icon-only; the long label clipped; the list wears the "
          "selected row's tint; the favorites' rows read selected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
