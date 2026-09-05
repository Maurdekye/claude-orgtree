"""Real-browser geometry for the docket list after the name change (w31b77251).

WHY THIS EXISTS AND THE COMPONENT TESTS DO NOT COVER IT. The user's objection
was a LAYOUT one, twice, from screenshots: the item name was a padded bordered
button, it ate the row's second line, and the agent name beside it truncated to
"c…". Every DOM test passed throughout — the name WAS present, the updater WAS
present. Presence is not the property that was wrong.

So this plants the REAL <DocketModal/> markup (via docket_dump.mjs) against the
REAL styles.css, in a real browser, at the width the panel actually gets, and
measures:

  * the item name is TEXT, not a control — no button anywhere in a row's name
    line, and none beside the detail name except the agent jump
  * no row's content overflows its own box horizontally
  * the last-updater name is not squeezed to a stub: it gets a real share of
    the row, and the ellipsis (if any) leaves several characters
  * a mention inside the description renders inline — same line box as the
    words around it, not a block that breaks the sentence

    python -B tests/docket_layout_probe.py
    python -B tests/docket_layout_probe.py --expect-fail chip   # planted mutant
    python -B tests/docket_layout_probe.py --shot out.png       # look at it
"""
from __future__ import annotations

import argparse
import pathlib
import subprocess
import tempfile

from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).resolve().parent
CSS = HERE.parent / "src" / "styles.css"

# The panel is a modal over the canvas; the frame gives it the width it really
# gets and nothing else. NARROW ON PURPOSE — the user's screenshot was a narrow
# list, and a wide viewport hides exactly the crowding being tested.
FRAME = """
html, body { margin: 0; background: var(--bg); }
body > .overlay { position: static; }
.settings.wide.docket-modal { width: 760px; max-width: 760px; }
.mailpane { height: 460px; }
"""

# Known negatives. Each is a way the rejected design comes back, or a way the
# row silently degrades; the probe must FAIL every one of them.
CONTROLS = {
    # ⚠ A CONTROL MUST BE ABLE TO FIRE. The first version of the "chip" control
    # put a border back on the name and the probe still passed, because the
    # check it was aimed at looked for the removed element's CLASS — and no
    # stylesheet can add a class. The checks below are GEOMETRIC for exactly
    # that reason: they measure the box, so a stylesheet can break them.
    #
    # the exact thing the user removed: a padded, bordered name
    "chip": """
.docket-rowname, .docket-slug-text {
  border: 1px solid var(--line-soft); background: var(--input);
  border-radius: 3px; padding: 0 5px; max-width: 190px; }
""",
    # the consequence they actually saw — the updater squeezed to a stub
    "squeeze": """
.docket-row .l2 .docket-status { min-width: 190px; }
""",
    # a mention that breaks the sentence it sits in
    "blockref": """.docket-modal .docket-ref { display: block; padding: 7px 15px; }""",
    # the run-together sub-line: "In progressclickable-docket-references-…"
    "runtogether": """.docket-pane-sub .docket-slug-text { margin: 0; }""",
}

# what a name may cost in chrome before it is a control again, and how much of
# an agent's name must still be readable. The floor is not a wish: the worst
# row measured here is the ATTENTION row, whose Dismiss button shares the line,
# and it shows 45px / 50% of "codex-checklist". The bar sits just under that on
# purpose, so this fails the moment that row gets worse — and the `squeeze`
# control drives it to a fraction of it.
UPDATER_MIN_PX = 40
UPDATER_MIN_SHOWN = 0.45
SUB_GAP_MIN_PX = 3

MEASURE = r"""
() => {
  const bad = []
  const px = (v) => parseFloat(v) || 0
  const rows = [...document.querySelectorAll('.mailrow.docket-row')]
  if (rows.length < 4) bad.push(`only ${rows.length} rows rendered`)

  for (const r of rows) {
    const name = r.querySelector('.l1 .mfrom')
    const label = (name?.textContent ?? '?')
    if (!name) { bad.push('a row has no name element'); continue }

    // 1. THE NAME IS TEXT, MEASURED AS SUCH — not a bordered, padded box, and
    //    not a button. Checking the class alone would pass on a restyled span.
    if (name.tagName === 'BUTTON') bad.push(`${label}: the row name is a button`)
    if (r.querySelector('.l1 button')) bad.push(`${label}: a control is in the name line`)
    const ncs = getComputedStyle(name)
    if (px(ncs.borderTopWidth) || px(ncs.borderLeftWidth)) {
      bad.push(`${label}: the row name is boxed again (border)`)
    }
    if (px(ncs.paddingLeft) > 1 || px(ncs.paddingTop) > 1) {
      bad.push(`${label}: the row name is padded like a chip`)
    }

    // 2. nothing overflows the row horizontally
    const rb = r.getBoundingClientRect()
    for (const child of r.querySelectorAll('.l1 > *, .l2 > *')) {
      const cb = child.getBoundingClientRect()
      if (cb.right > rb.right + 0.5 || cb.left < rb.left - 0.5) {
        bad.push(`${label}: ${child.className || child.tagName} spills outside the row`)
      }
    }

    // 3. the updater is not squeezed to a stub. It is allowed to ellipsise — a
    //    long agent name should — but "c…" is the defect that started this.
    const up = r.querySelector('.l2 .docket-actor-name')
    if (up) {
      const w = up.getBoundingClientRect().width
      const shown = up.scrollWidth > 0 ? w / up.scrollWidth : 1
      if (w < __UP_PX__) {
        bad.push(`${label}: updater "${up.textContent}" is only ${w.toFixed(0)}px wide`)
      }
      if (shown < __UP_SHOWN__) {
        bad.push(`${label}: updater "${up.textContent}" shows only `
          + `${(shown * 100).toFixed(0)}% of its name`)
      }
    }
  }

  // 4. the detail name is text too, and does not run into the status word
  const sub = document.querySelector('.docket-pane-sub')
  const slug = sub?.querySelector('.docket-slug-text')
  const status = sub?.querySelector('.docket-status')
  if (!slug || !status) {
    bad.push('the detail sub-line lost its status or its name — probe inert')
  } else {
    if (slug.tagName === 'BUTTON') bad.push('the detail name is a button again')
    const scs = getComputedStyle(slug)
    if (px(scs.borderTopWidth) || px(scs.paddingLeft) > 1) {
      bad.push('the detail name is a boxed chip again')
    }
    const gap = slug.getBoundingClientRect().left - status.getBoundingClientRect().right
    if (gap < __SUB_GAP__) {
      bad.push(`the status word and the name run together (${gap.toFixed(1)}px apart)`)
    }
  }

  // 5. a mention is inline prose, not a block that breaks the sentence
  const body = document.querySelector('.docket-desc-body')
  const ref = body?.querySelector('.docket-ref')
  if (!ref) {
    bad.push('no mention rendered in the description — the probe is inert')
  } else {
    const rb = ref.getBoundingClientRect()
    const rcs = getComputedStyle(ref)
    if (rcs.display !== 'inline') bad.push(`the mention is display:${rcs.display}`)
    if (px(rcs.paddingLeft) > 1) bad.push('the mention carries button padding')
    const line = px(getComputedStyle(body).lineHeight) || 20
    if (rb.height > line * 1.6) {
      bad.push(`the mention is ${rb.height.toFixed(0)}px tall on an `
        + `${line.toFixed(0)}px line`)
    }
    // and the two decoys in the same sentence must NOT have become links
    const linked = [...body.querySelectorAll('.docket-ref')].map((e) => e.textContent)
    if (linked.length !== 1) {
      bad.push(`${linked.length} mentions linked in a sentence with one real `
        + `one: ${linked.join(', ')}`)
    }
  }
  return bad
}
"""


def dump() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp) / "docket.html"
        subprocess.run(["node", str(HERE / "docket_dump.mjs"), str(out)],
                       check=True, capture_output=True)
        return out.read_text(encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--expect-fail", nargs="?", const="chip",
                    choices=sorted(CONTROLS),
                    help="run a known-negative control; the probe must FAIL it")
    ap.add_argument("--shot")
    args = ap.parse_args()
    fragment = dump()
    css = CSS.read_text(encoding="utf-8")
    html = (f"<!doctype html><meta charset='utf-8'><style>{css}\n{FRAME}\n"
            f"{CONTROLS.get(args.expect_fail or '', '')}</style>\n{fragment}")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 820, "height": 720},
                                device_scale_factor=2)
        page.set_content(html)
        page.wait_for_timeout(150)
        bad = page.evaluate(MEASURE
                            .replace('__UP_PX__', str(UPDATER_MIN_PX))
                            .replace('__UP_SHOWN__', str(UPDATER_MIN_SHOWN))
                            .replace('__SUB_GAP__', str(SUB_GAP_MIN_PX)))
        # measured separately so a green run states NUMBERS, not "fine"
        facts = page.evaluate(
            "() => {"
            "  const up = [...document.querySelectorAll('.l2 .docket-actor-name')];"
            "  const w = up.map((e) => e.getBoundingClientRect().width);"
            "  const list = document.querySelector('.mailer-list');"
            "  return { rows: document.querySelectorAll('.mailrow.docket-row').length,"
            "    refs: document.querySelectorAll('.docket-ref').length,"
            "    listW: list ? list.getBoundingClientRect().width : 0,"
            "    minUpdater: w.length ? Math.min(...w) : 0 } }")
        if args.shot:
            page.screenshot(path=args.shot, full_page=True)
            print(f"saved {args.shot}")
        browser.close()
    if args.expect_fail:
        if not bad:
            print(f"CONTROL FAILED — the '{args.expect_fail}' mutant passed the probe")
            return 1
        print(f"CONTROL OK — '{args.expect_fail}' is caught: " + "; ".join(bad[:3])
              + (f" … (+{len(bad) - 3})" if len(bad) > 3 else ""))
        return 0
    if bad:
        print("\n".join("FAIL: " + b for b in bad))
        return 1
    print(f"OK — {facts['rows']} rows in a {facts['listW']:.0f}px list; every item "
          f"name is plain text with no chip and no control; nothing spills out of "
          f"its row; the narrowest last-updater name is {facts['minUpdater']:.0f}px "
          f"and readable; {facts['refs']} mentions render inline in the detail pane")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
