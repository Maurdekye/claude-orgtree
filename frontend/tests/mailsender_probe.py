"""mailsender_probe.py — STEP 2: is the sender's model card actually WEARABLE
where it was just put, and is the name actually CLICKABLE there?

Two surfaces gained the shared identity — the mail LIST ROW and the desk
transcript's mail card — and the interesting claims about both are claims the
repo's jsdom suite cannot make. jsdom has no box model, no cascade and no hit
testing, so "the row did not grow", "the `.settings button` chrome was reset"
and "the name is the thing under the cursor" are questions it answers by
abstaining, and an abstention reads exactly like a pass.

So this measures in a real engine — the system Edge, headless, via Playwright
(the same channel="msedge" recipe tools/ui_probe.py uses, so no browser
download). It opens a file:// URL and touches no backend, no port, no user
data.

    node tests/mailsender_dump.mjs /tmp/ms.html
    python tests/mailsender_probe.py /tmp/ms.html [--css PATH] [--shot PNG]
                                     [--expect-fail] [--break-chip]

WHAT IT CHECKS
  1. rowheight  — a row with a model chip is no taller than a row without one.
                  The dump carries both (an agent sender and an @system
                  notice), so this is a COMPARISON inside one page, not an
                  assertion about a number somebody chose.
  2. rowchrome  — the name button inside the row carries no button chrome.
                  `.settings button` sets `font-size: 14px; padding: 7px 15px;
                  border-radius: 6px`, and the user's inbox IS a `.settings`
                  modal, so an unreset button turns an 11px row into a 35px
                  one. That has happened in this repo before (see the
                  `.docket-ref` comment in styles.css).
  3. rowtype    — the name is set in the ROW's type, not `.cc-name`'s 12.5px,
                  measured against the plain `@system` name on the same page.
  4. rowchip    — the chip fits inside the row's first line.
  5. rowhit     — THE CLICK ACTUALLY LANDS ON THE NAME.
                  `document.elementFromPoint` at the name's own centre must
                  return the button or something inside it. A control point on
                  the row's preview line must return something that is NOT the
                  button, so the check distinguishes "the name is hittable"
                  from "everything hits the name".
  6. cardhead   — the card's chip and name sit inside the header's line box and
                  the chip comes before the name.
  7. carddot    — ⚠ NO STRAY SEPARATOR. `.turn-mail-head span:not(:last-child)
                  ::after` puts a '·' after ANY span, and BOTH halves of an
                  identity are spans — so left alone it punctuates between the
                  model card and the name it belongs to ("O · astra your
                  superior · …"). Read off the real ::after content.
  8. cardtype   — the name kept the header's own type: same size as the `<b>`
                  metadata the header already had… which is the one thing here
                  with no `<b>` left to compare against, so it is compared to
                  the header's declared 11.5px and to the plain metadata spans
                  beside it (10.5px), and must match neither the 12.5px
                  `.cc-name` default nor the 14px `.settings button`.

THE CONTROL. `--css <a sheet without these rules>` must FAIL, and
`--expect-fail` makes that the passing outcome:

    git show 1ac1dc7:frontend/src/styles.css > /tmp/pre.css
    python tests/mailsender_probe.py /tmp/ms.html --css /tmp/pre.css --expect-fail

⚠ NAME THE PRE-SERIES COMMIT, NOT `HEAD`. Once this work is committed `HEAD`
IS the new sheet, the control passes, and the probe then reports that it proves
nothing — which is what it did to this author's predecessor once. 1ac1dc7 is
the commit these two surfaces were built on.

Pairing today's markup with the previous sheet is sound HERE precisely because
the markup change is what the old sheet mishandles: the old sheet still styles
`.cc-name`, `.sender .tier` and `.turn-mail-head span::after`, so it shows what
those rules do to these elements without the new resets — which is the whole
claim.

⚠ AND TWO MORE CONTROLS, because the sheet control above does NOT exercise
every check and a check nobody has watched fail is not a check.

`--break-chip` grows both chips. It fires check 1 (the row grows with the
chip) — and, measured, NOTHING ELSE: a grown chip grows the line with it, so
"the chip is inside its line" stays true the whole time.

`--poke-chip` therefore exists as well: relative positioning moves the chip's
box out of its line WITHOUT changing layout, which is the only way to make the
"pokes out" halves of checks 4 and 6 fire. Both must FAIL:

    python tests/mailsender_probe.py /tmp/ms.html --break-chip --expect-fail
    python tests/mailsender_probe.py /tmp/ms.html --poke-chip --expect-fail

WHAT NONE OF THESE COVER, said plainly: check 5 (the hit test) has no control
sheet that breaks it — no CSS in this repo covers the name — so it is watched
only through its own anti-vacuity clause (the row's preview line must NOT hit
the name). And a `.settings button` reset is proven for the USER's inbox
cascade, which is the one the dump renders; a node's own mailbox lives in a
`.desk-body` instead and is not measured here.
"""
import argparse
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_CSS = HERE.parent / "src" / "styles.css"

MEASURE = """
() => {
  const box = (el) => {
    if (!el) return null
    const r = el.getBoundingClientRect()
    const s = getComputedStyle(el)
    return {
      x: r.x, y: r.y, w: r.width, h: r.height, top: r.top, bottom: r.bottom,
      left: r.left, right: r.right, mid: r.top + r.height / 2,
      cx: r.left + r.width / 2,
      family: s.fontFamily, size: parseFloat(s.fontSize),
      weight: s.fontWeight, display: s.display,
      padTop: parseFloat(s.paddingTop), padLeft: parseFloat(s.paddingLeft),
      padBottom: parseFloat(s.paddingBottom), padRight: parseFloat(s.paddingRight),
      radius: parseFloat(s.borderTopLeftRadius),
      lineHeight: s.lineHeight,
    }
  }
  const rows = [...document.querySelectorAll('.mailrow')]
  // the row whose sender is an agent (it has a chip) and the row whose sender
  // is a sentinel (it has none) — the pair the height comparison rests on
  const chipRow = rows.find((r) => r.querySelector('.mfrom .tier'))
  // ⚠ THE CONTROL ROW IS A ROW WITH AN IDENTITY AND NO CHIP — same two lines,
  // same type, differing in exactly the thing under test. NOT the system
  // notice, which is deliberately one line shorter and a size smaller.
  const plainRow = rows.find((r) => r.querySelector('.mfrom .cc-name')
    && !r.querySelector('.mfrom .tier'))
  const rowName = chipRow && chipRow.querySelector('.mfrom .cc-name')
  const rowChip = chipRow && chipRow.querySelector('.mfrom .tier')
  const rowL1 = chipRow && chipRow.querySelector('.l1')
  const rowL2 = chipRow && chipRow.querySelector('.l2')
  const plainName = plainRow && plainRow.querySelector('.mfrom > *')
  // ⚠ THE TWO IDENTITY SHAPES DIFFER, and a check that assumed one of them
  // reported a false failure the first time this ran. `AgentName` puts the
  // chip BESIDE the name; `SenderChip` (the user's inbox) nests both inside
  // the button, so the name element's own box STARTS at the chip. What is
  // true either way is that the chip comes before the TEXT — so measure the
  // innermost thing carrying the name.
  const rowText = chipRow
    && (chipRow.querySelector('.mfrom .sender b') || rowName)
  // …and the row's own type, read off the line rather than off a neighbouring
  // row: the plain row in this dump is a SYSTEM NOTICE, which is deliberately
  // set smaller, so comparing to it fails for a reason that is not this work.
  const rowTime = chipRow && chipRow.querySelector('.mtime')

  const head = document.querySelector('.turn-mail-head')
  const cardName = head && head.querySelector('.cc-name')
  const cardChip = head && head.querySelector('.tier')
  const cardMeta = head && [...head.querySelectorAll('span')]
    .find((e) => !e.classList.contains('tier') && !e.classList.contains('cc-name'))

  // ⚠ THE HIT TEST. Not "does the element exist" but "is it what the pointer
  // finds there". Returns the tag/class path so a failure names the thing that
  // was on top instead.
  const hit = (el, dx = 0, dy = 0) => {
    if (!el) return null
    const r = el.getBoundingClientRect()
    const t = document.elementFromPoint(r.left + r.width / 2 + dx,
                                        r.top + r.height / 2 + dy)
    if (!t) return { none: true }
    return {
      tag: t.tagName.toLowerCase(),
      cls: t.className && t.className.baseVal === undefined ? String(t.className) : '',
      inName: Boolean(rowName && (t === rowName || rowName.contains(t))),
      inRow: Boolean(chipRow && (t === chipRow || chipRow.contains(t))),
    }
  }
  // the separator the header's own rule paints after a span
  const after = (el) => (el
    ? getComputedStyle(el, '::after').content : null)

  return {
    rowChipRow: box(chipRow), rowPlainRow: box(plainRow),
    rowName: box(rowName), rowChip: box(rowChip), rowL1: box(rowL1),
    rowPlainName: box(plainName), rowText: box(rowText), rowTime: box(rowTime),
    cardHead: box(head), cardName: box(cardName), cardChip: box(cardChip),
    cardMeta: box(cardMeta),
    hitName: hit(rowName), hitBody: hit(rowL2),
    afterChip: after(cardChip), afterName: after(cardName),
  }
}
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("html")
    ap.add_argument("--css", default=str(DEFAULT_CSS))
    ap.add_argument("--shot")
    ap.add_argument("--expect-fail", action="store_true")
    ap.add_argument("--break-chip", action="store_true",
                    help="grow both chips: check 1's own control, which must "
                         "FAIL")
    ap.add_argument("--poke-chip", action="store_true",
                    help="shove both chips out of their line boxes without "
                         "changing layout: the control for the 'pokes out' "
                         "halves of checks 4 and 6, which must FAIL")
    a = ap.parse_args()

    css = pathlib.Path(a.css).resolve().read_text(encoding="utf-8")
    if a.break_chip:
        css += """
.mailrow .l1 .mfrom .tier, .mailrow .l1 .mfrom .sender .tier,
.desk-body .turn-mail-head .tier, .turn-mail-head .tier {
  width: 34px; height: 34px; font-size: 20px;
}
"""
    if a.poke_chip:
        # ⚠ WHY A SECOND CONTROL EXISTS. `--break-chip` GROWS the chip, and a
        # grown chip grows the line with it — so the row-height check fires and
        # the two "the chip is inside its line" assertions still pass, having
        # never been watched fail. Relative positioning moves the box without
        # touching layout, which is the only way to make a chip leave its line
        # while the line stays where it was.
        css += """
.mailrow .l1 .mfrom .tier, .mailrow .l1 .mfrom .sender .tier,
.desk-body .turn-mail-head .tier, .turn-mail-head .tier {
  position: relative; top: 16px;
}
"""
    body = pathlib.Path(a.html).resolve().read_text(encoding="utf-8")
    page = (f"<!doctype html><meta charset=utf-8><style>{css}</style>"
            f"<body class='dark'>{body}</body>")
    tmp = pathlib.Path(a.html).with_suffix(".page.html")
    tmp.write_text(page, encoding="utf-8")

    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge", headless=True)
        ctx = b.new_context(viewport={"width": 1280, "height": 900},
                            device_scale_factor=2)
        pg = ctx.new_page()
        pg.goto(tmp.as_uri(), wait_until="load")
        pg.wait_for_selector(".mailrow", state="attached", timeout=8000)
        m = pg.evaluate(MEASURE)
        if a.shot:
            # the whole page: the inbox modal is positioned and escapes the
            # `.viewport` box, so an element screenshot of it clips the rows
            # this probe is about
            pg.screenshot(path=a.shot, full_page=True)
            print("wrote", a.shot)
            # …and the transcript card on its own: the inbox modal sits over
            # the desk, so the full page shows the ROW surface and hides the
            # CARD one, and a screenshot that shows only half the work is a
            # screenshot that invites the wrong conclusion
            # (the modal is painted OVER the card, and an element screenshot
            # captures the page region — not the element — so it has to go.
            # Done after every measurement, never before.)
            pg.evaluate("() => document.querySelectorAll('.overlay')"
                        ".forEach((o) => o.remove())")
            card = pg.query_selector(".turn-mail")
            if card:
                p2 = pathlib.Path(a.shot).with_name(
                    pathlib.Path(a.shot).stem + "-card.png")
                card.screenshot(path=str(p2))
                print("wrote", p2)
        ctx.close()
        b.close()

    # ⚠ NOTHING TO MEASURE IS A FAILURE, NOT A PASS. Every check below reads
    # two boxes; a missing one would make every comparison vacuously true.
    fails = []
    missing = [k for k, v in m.items() if v is None]
    if missing:
        print(f"  MISSING: {', '.join(missing)}")
        fails.append(f"missing: the probe never found {', '.join(missing)} — "
                     "nothing was measured")
        print("\nFAIL" if not a.expect_fail else "\nfailed as required")
        return 0 if a.expect_fail else 1

    cr, pr = m["rowChipRow"], m["rowPlainRow"]
    rn, rc, l1, pn = m["rowName"], m["rowChip"], m["rowL1"], m["rowPlainName"]
    ch, cn, cc, cm = m["cardHead"], m["cardName"], m["cardChip"], m["cardMeta"]

    print(f"  row w/ chip : h={cr['h']:.1f}  l1 h={l1['h']:.1f}")
    print(f"  row w/o chip: h={pr['h']:.1f}")
    print(f"  row name    : {rn['size']}px {rn['family'][:26]!r} w={rn['weight']} "
          f"pad={rn['padTop']}/{rn['padRight']}/{rn['padBottom']}/{rn['padLeft']} "
          f"radius={rn['radius']}")
    print(f"  plain name  : {pn['size']}px {pn['family'][:26]!r}")
    print(f"  row chip    : {rc['w']:.1f}x{rc['h']:.1f}")
    print(f"  card head   : h={ch['h']:.1f}  name {cn['size']}px  "
          f"chip {cc['w']:.1f}x{cc['h']:.1f}  meta {cm['size']}px")
    print(f"  hit at name : {m['hitName']}")
    print(f"  hit at body : {m['hitBody']}")
    print(f"  ::after chip={m['afterChip']!r} name={m['afterName']!r}")

    # 1. rowheight — THE CHIP DID NOT MAKE THE ROW TALLER. Two rows in one
    # page, identical but for the chip (the control's sender is not in the
    # tree, so it draws a name and nothing else). This is the check
    # `--break-chip` exists to make fail.
    if cr["h"] > pr["h"] + 0.5:
        fails.append(f"rowheight: the row WITH a model chip is {cr['h']:.1f}px "
                     f"where the same row without one is {pr['h']:.1f}px — "
                     "the chip is setting the row's height")
    if l1["h"] > rc["h"] + 6:
        fails.append(f"rowheight: the row's first line is {l1['h']:.1f}px "
                     f"around a {rc['h']:.1f}px chip — something in the "
                     "identity is padding the line")

    # 2. rowchrome — no `.settings button` chrome survived into the row
    for side in ("padTop", "padRight", "padBottom", "padLeft"):
        if rn[side] > 0.01:
            fails.append(f"rowchrome: the row's name carries {side}={rn[side]}px "
                         "— the `.settings button` chrome was not reset")
    if rn["radius"] > 0.01:
        fails.append(f"rowchrome: the row's name has a {rn['radius']}px corner "
                     "radius — it is drawn as a button inside a list row")

    # 3. rowtype — the ROW's type, not `.cc-name`'s 12.5px.
    # ⚠ MEASURED AGAINST THE ROW'S OWN LINE, not against the plain row: that
    # one is a system notice and is deliberately set a size smaller, so it
    # reported a failure that had nothing to do with this work. `.mtime` sits
    # in the SAME `.l1` and inherits the same type.
    tm = m["rowTime"]
    if abs(rn["size"] - l1["size"]) > 0.01:
        fails.append(f"rowtype: the agent name is {rn['size']}px in a "
                     f"{l1['size']}px row line")
    if abs(rn["size"] - tm["size"]) > 0.01:
        fails.append(f"rowtype: the agent name is {rn['size']}px where the "
                     f"timestamp on the same line is {tm['size']}px")
    if rn["family"] != tm["family"]:
        fails.append(f"rowtype: the agent name is in {rn['family'][:30]!r} and "
                     f"the rest of the line in {tm['family'][:30]!r}")
    # …and the face this reset exists to override is really on this page, or
    # the check above is a distinction between two identical things
    if cn["family"] == m["cardMeta"]["family"] and rn["size"] == 12.5:
        fails.append("rowtype: nothing on this page distinguishes `.cc-name`'s "
                     "own type — check 3 cannot fail")

    # 4. rowchip — inside the row's first line
    if rc["h"] > l1["h"] + 0.5:
        fails.append(f"rowchip: the chip is {rc['h']:.1f}px in a {l1['h']:.1f}px "
                     "line — it sets the row's height")
    if rc["top"] < l1["top"] - 0.5 or rc["bottom"] > l1["bottom"] + 0.5:
        fails.append("rowchip: the chip pokes out of the row's first line "
                     f"(chip {rc['top']:.1f}..{rc['bottom']:.1f}, line "
                     f"{l1['top']:.1f}..{l1['bottom']:.1f})")
    # ⚠ against the TEXT, not the name element: `SenderChip` nests the chip
    # inside the button, so the name's own box legitimately starts at the chip
    rt = m["rowText"]
    if rc["right"] > rt["left"] + 0.5 and rc["x"] >= rt["x"]:
        fails.append(f"rowchip: the chip (x={rc['x']:.1f}..{rc['right']:.1f}) "
                     f"is not before the name text (x={rt['x']:.1f})")

    # 5. rowhit — the click lands on the name, and not everywhere
    hn, hb = m["hitName"], m["hitBody"]
    if not hn or not hn.get("inName"):
        fails.append("rowhit: the point at the centre of the sender's name is "
                     f"not the name — elementFromPoint found {hn}")
    # ⚠ ANTI-VACUITY: if the name covered the whole row, check 5 would pass for
    # the wrong reason. The preview line must NOT hit the name.
    if hb and hb.get("inName"):
        fails.append("rowhit: the row's preview line ALSO hits the name — the "
                     "name is not a target, it is the whole row")
    if hb and not hb.get("inRow"):
        fails.append("rowhit: the control point does not even land in the row "
                     f"— the instrument is measuring the wrong place ({hb})")

    # 6. cardhead — the identity fits the transcript card's header line
    if cc["h"] > ch["h"] + 0.5:
        fails.append(f"cardhead: the chip is {cc['h']:.1f}px in a {ch['h']:.1f}px "
                     "header — it sets the card header's height")
    if cc["top"] < ch["top"] - 0.5 or cc["bottom"] > ch["bottom"] + 0.5:
        fails.append("cardhead: the chip pokes out of the header's box "
                     f"(chip {cc['top']:.1f}..{cc['bottom']:.1f}, head "
                     f"{ch['top']:.1f}..{ch['bottom']:.1f})")
    if cc["x"] >= cn["x"]:
        fails.append("cardhead: the chip is not before the name it belongs to")

    # 7. carddot — no separator between a chip and its own name
    for key, what in (("afterChip", "the model chip"), ("afterName", "the name")):
        v = m[key]
        if v and "·" in str(v):
            fails.append(f"carddot: a '·' is painted after {what} — the "
                         "header's separator rule matches any span, and both "
                         "halves of an identity are spans")

    # 8. cardtype — the header's own type, not the switchboard's or a button's
    if cn["size"] >= 12.5:
        fails.append(f"cardtype: the card's name is {cn['size']}px — that is "
                     "`.cc-name`'s 12.5px (or a button's 14px), not the "
                     "header's 11.5px")
    if cn["size"] <= cm["size"]:
        fails.append(f"cardtype: the name is {cn['size']}px against "
                     f"{cm['size']}px metadata — the sender no longer leads "
                     "the header")
    for side in ("padTop", "padRight", "padBottom", "padLeft"):
        if cn[side] > 0.01:
            fails.append(f"cardtype: the card's name carries {side}="
                         f"{cn[side]}px of button chrome")

    for f in fails:
        print("  FAIL", f)
    ok = not fails
    if a.expect_fail:
        print("\n" + ("failed as required — this probe can see a regression"
                      if not ok else
                      "PASSED WITH THE CONTROL SHEET — this probe proves "
                      "nothing; the sheet you passed already contains the "
                      "rules under test (did you use HEAD instead of the "
                      "pre-series commit?)"))
        return 0 if not ok else 1
    print("\n" + ("OK — both surfaces measured in a real engine" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
