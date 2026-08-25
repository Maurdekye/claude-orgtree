"""acctcols_probe.py — STEP 2: does the accounts panel actually line up?

The repo's frontend suite runs in jsdom, which implements NO CSS box model:
every offsetLeft it reports is 0 and every alignment assertion written there
would abstain. An abstention reads exactly like a pass, so this probe measures
in a real layout engine instead — the system Edge, headless, via Playwright
(same channel="msedge" recipe tools/ui_probe.py already uses, so no browser
download).

    python frontend/tests/acctcols_probe.py <panel.html> [--css PATH] [--shot PNG]

<panel.html> is the markup dumped by `tests/acctcols_dump.mjs`, i.e. the REAL
<AccountsPanel/> render — not a hand-copied approximation that could drift from
accounts.tsx without anyone noticing. --css defaults to src/styles.css; the
known-negative control passes the PRE-fix sheet and requires this probe to
FAIL, which is the only thing that establishes the probe can fail at all.

Touches no orgtree backend, no port, no user data: it opens a file:// URL.

RUNNING IT (from frontend/)
    node tests/acctcols_dump.mjs /tmp/acct.html
    python tests/acctcols_probe.py /tmp/acct.html --shot /tmp/after.png
and the control, against whatever sheet predates the change under test:
    git show <ref>:frontend/src/styles.css > /tmp/old.css
    python tests/acctcols_probe.py /tmp/acct.html --css /tmp/old.css --expect-fail

Pairing today's markup with an old sheet is sound HERE because the markup
change that shipped with this probe (dropping a `grow` class) was inert in both
sheets — `.grow` matches no input in either. It is NOT sound in general: if a
future change moves an element between columns, dump the markup from the old
ref too (add a detached worktree at it, copy these two files in, dump there).
That is how the 2026-08-25 control was run, and it failed on five counts —
usage buttons 248px apart, the check button 248px off its column, and a painted
border on the primary row.

WHAT IT CHECKS
  1. columns  — every row's usage button shares one left/right edge, ditto the
                delete buttons, the grips and the field boxes; the ✓ button
                spans the usage+delete columns exactly.
  2. noborder — the primary row paints no border, AND still occupies the same
                box as the key rows (a `border: none` "fix" would pass the
                first half and silently break the first check by 1px).

KNOWN DIVERGENCE FROM PRODUCTION, STATED RATHER THAN HIDDEN: the icons are MUI
components whose sizing rules are injected at runtime by emotion (the
`css-*-MuiSvgIcon-root` classes in the dump resolve to nothing in a static
file). ICON_SHIM below restores what those rules do — `font-size: inherit` and
a 1em square. It cannot affect the checks either way, because `.acct-btn` is
`flex: none` at a fixed 27x27, but leaving the icons at the SVG default 300x150
would make any screenshot unreadable.
"""

import argparse
import json
import os
import pathlib
import sys
import tempfile

from playwright.sync_api import sync_playwright

# ⚠ this console is cp1252. Measured: the FIRST control run died with
# UnicodeEncodeError halfway through printing its own findings — a probe that
# crashes while reporting a failure is a probe that hides it, and the crash
# lands on the "control is broken" branch too, which is the one path that must
# never be silent. Never rely on the ambient code page.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = pathlib.Path(__file__).resolve().parent
FRONTEND = HERE.parent

ICON_SHIM = """
/* what emotion injects for MuiSvgIcon-fontSizeInherit at runtime */
svg.MuiSvgIcon-root { width: 1em; height: 1em; font-size: inherit;
  fill: currentColor; user-select: none; flex-shrink: 0; }
/* the app mounts the panel over a canvas; a plain page is close enough for
   the panel's own internal geometry, which is all this probe reads */
body { margin: 0; }
"""

# sub-pixel slack. Fractional layout is real (flex distributes remainders), but
# a column that is off by a whole pixel is a column that is off.
TOL = 0.51


def build_page(markup: str, css: str) -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'><style>\n"
        + css + "\n" + ICON_SHIM + "\n</style></head><body>\n"
        + markup + "\n</body></html>"
    )


MEASURE = """() => {
  const box = (el) => {
    const r = el.getBoundingClientRect();
    return { left: r.left, right: r.right, top: r.top, width: r.width, height: r.height };
  };
  const all = (sel) => [...document.querySelectorAll(sel)].map(box);
  const rows = [...document.querySelectorAll('.acct-row')];
  // the field column: whichever element occupies it on each row
  const fields = rows.map((r) => {
    const f = r.querySelector('.acct-email, input');
    return f ? box(f) : null;
  });
  const primary = document.querySelector('.acct-primary');
  const cs = getComputedStyle(primary);
  const key = document.querySelector('.acct-key');
  const kcs = getComputedStyle(key);
  return {
    usage: all('.acct-usage-btn'),
    del: all('.acct-del'),
    add: all('.acct-add'),
    grips: all('.acct-grip'),
    fields,
    rowBoxes: rows.map(box),
    rowClasses: rows.map((r) => r.className),
    primaryStyle: {
      borderTopColor: cs.borderTopColor, borderLeftColor: cs.borderLeftColor,
      borderRightColor: cs.borderRightColor, borderBottomColor: cs.borderBottomColor,
      borderTopWidth: cs.borderTopWidth, borderLeftWidth: cs.borderLeftWidth,
      borderStyle: cs.borderTopStyle,
    },
    keyStyle: {
      borderTopColor: kcs.borderTopColor, borderTopWidth: kcs.borderTopWidth,
    },
  };
}"""


def spread(boxes, edge):
    vals = [b[edge] for b in boxes]
    return max(vals) - min(vals), vals


def check(m, verbose=True):
    """returns list of failure strings (empty == aligned)"""
    fails = []
    say = print if verbose else (lambda *a, **k: None)

    # --- 0. the dump is the loaded panel, not a placeholder -----------------
    if len(m["rowBoxes"]) != 5 or len(m["usage"]) != 4:
        return [f"markup is not the loaded panel: {len(m['rowBoxes'])} rows, "
                f"{len(m['usage'])} usage buttons (want 5 / 4)"]

    # --- 1. the four columns ------------------------------------------------
    for name, boxes, want in (
        ("usage button", m["usage"], 4),
        ("delete button", m["del"], 3),
        ("grip", m["grips"], 5),
        ("field", [f for f in m["fields"] if f], 5),
    ):
        if len(boxes) != want:
            fails.append(f"{name}: found {len(boxes)}, expected {want}")
            continue
        for edge in ("left", "right"):
            d, vals = spread(boxes, edge)
            say(f"  {name:<14} {edge:<5} {[round(v, 2) for v in vals]}  spread={d:.2f}")
            if d > TOL:
                fails.append(f"{name}s disagree on {edge} by {d:.2f}px: "
                             f"{[round(v, 2) for v in vals]}")

    # --- 2. the ✓ spans the usage+delete columns ---------------------------
    if len(m["add"]) == 1 and m["usage"] and m["del"]:
        add, u, d = m["add"][0], m["usage"][0], m["del"][0]
        say(f"  add button     left {add['left']:.2f} (usage {u['left']:.2f}) "
            f"right {add['right']:.2f} (del {d['right']:.2f})")
        if abs(add["left"] - u["left"]) > TOL:
            fails.append(f"check button starts {add['left'] - u['left']:.2f}px off "
                         "the usage column")
        if abs(add["right"] - d["right"]) > TOL:
            fails.append(f"check button ends {add['right'] - d['right']:.2f}px off "
                         "the delete column")

    # --- 3. the primary row paints no border -------------------------------
    ps = m["primaryStyle"]
    say(f"  primary border  {ps['borderStyle']} {ps['borderTopWidth']} "
        f"{ps['borderTopColor']}")
    painted = [side for side in ("Top", "Left", "Right", "Bottom")
               if not is_invisible(ps[f"border{side}Color"])]
    if painted:
        fails.append("primary row still paints a border on "
                     + "/".join(painted) + f" ({ps['borderTopColor']})")

    # --- 4. ...WITHOUT losing its 1px box ----------------------------------
    # `border: none` would satisfy check 3 and quietly shift this row's whole
    # contents 1px left and up, i.e. undo check 1. The width has to stay.
    if ps["borderTopWidth"] != m["keyStyle"]["borderTopWidth"]:
        fails.append(
            f"primary border-width is {ps['borderTopWidth']} but the key rows' "
            f"is {m['keyStyle']['borderTopWidth']} — the row's contents will sit "
            "off-column by the difference")
    rl, _ = spread(m["rowBoxes"], "left")
    rw = max(b["width"] for b in m["rowBoxes"]) - min(b["width"] for b in m["rowBoxes"])
    say(f"  row boxes       left spread={rl:.2f} width spread={rw:.2f}")
    if rl > TOL or rw > TOL:
        fails.append(f"row boxes disagree: left spread {rl:.2f}px, "
                     f"width spread {rw:.2f}px")
    return fails


def is_invisible(color: str) -> bool:
    c = color.replace(" ", "").lower()
    if c in ("transparent", "rgba(0,0,0,0)"):
        return True
    if c.startswith("rgba("):
        try:
            return float(c[5:-1].split(",")[3]) == 0.0
        except (IndexError, ValueError):
            return False
    return False


def run(markup_path, css_path, shot=None, width=1600, height=950, verbose=True):
    markup = pathlib.Path(markup_path).read_text(encoding="utf-8")
    css = pathlib.Path(css_path).read_text(encoding="utf-8")
    fd, page_path = tempfile.mkstemp(suffix=".html", dir=str(FRONTEND / "node_modules"))
    os.close(fd)
    pathlib.Path(page_path).write_text(build_page(markup, css), encoding="utf-8")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page(viewport={"width": width, "height": height})
            page.goto(pathlib.Path(page_path).as_uri())
            page.wait_for_selector(".acct-row", timeout=8000)
            m = page.evaluate(MEASURE)
            if shot:
                page.locator(".acct-panel").screenshot(path=shot)
                if verbose:
                    print("saved", shot)
            browser.close()
    finally:
        os.unlink(page_path)
    if verbose:
        print(f"-- measured at {width}x{height} against {css_path}")
    return m, check(m, verbose)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("markup")
    ap.add_argument("--css", default=str(FRONTEND / "src" / "styles.css"))
    ap.add_argument("--shot")
    ap.add_argument("--width", type=int, default=1600)
    # --expect-fail is the KNOWN-NEGATIVE CONTROL: run the pre-fix sheet
    # through the identical probe and require it to fail. Without this the
    # green run above proves only that the probe is capable of printing
    # "aligned", not that it is capable of noticing anything.
    ap.add_argument("--expect-fail", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    m, fails = run(a.markup, a.css, a.shot, width=a.width)
    if a.json:
        print(json.dumps(m, indent=1))
    if a.expect_fail:
        if fails:
            print(f"\nCONTROL OK — this sheet fails, as it must ({len(fails)}):")
            for f in fails:
                print("   ·", f)
            return 0
        print("\n⚠ CONTROL BROKEN: the pre-fix sheet PASSED this probe. The "
              "probe is not measuring what it claims to; every green run "
              "above is vacuous.")
        return 1
    if fails:
        print(f"\nFAIL ({len(fails)}):")
        for f in fails:
            print("   ·", f)
        return 1
    print("\nOK — columns align and the primary row paints no border")
    return 0


if __name__ == "__main__":
    sys.exit(main())
