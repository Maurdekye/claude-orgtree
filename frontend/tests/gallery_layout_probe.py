"""Real-browser geometry for the presented-documents gallery.

The gallery was redesigned (user request 2026-09-03) to resemble the MAIL UI:
entries on the left with title/agent/time, the scrollable document viewer on
the right. That is a CSS-layout claim, and the component tests — which assert
on the DOM — cannot see it: a panel whose right pane has collapsed to zero
width, or whose viewer does not scroll, passes every one of them.

This plants the gallery's markup against the REAL styles.css and measures.

    python -B tests/gallery_layout_probe.py
    python -B tests/gallery_layout_probe.py --expect-fail   # planted mutant
    python -B tests/gallery_layout_probe.py --shot out.png  # look at it
"""
from __future__ import annotations

import argparse
import pathlib

from playwright.sync_api import sync_playwright


CSS = pathlib.Path(__file__).resolve().parents[1] / "src" / "styles.css"

# the failure this is built to catch: a right pane that never gets its own
# scroll, so a long document stretches the panel instead of scrolling in place
MUTANT = """
.mailer-read { overflow-y: visible !important; }
.settings .mailpane { height: auto !important; }
"""

TIERS = ["opus", "sonnet", "haiku", "fable", "opus", "sonnet"]

# three ACTIVE agents' rows (accent flare) above three RETIRED ones (slightly
# greyed) — the exact adjacency the coordinator asked to eyeball: two
# emphasis steps on one axis, or two visual languages stacked?
ROWS = "".join(f"""
  <div class="mailrow doc-gallery-row {'active' if i < 3 else 'past'}{' on' if i == 1 else ''}">
    <div class="l1">
      <span class="mfrom">a presented document with a fairly long title {i}</span>
      <span class="mtime">{i + 1}h</span>
    </div>
    <div class="l2">
      <span class="tier t-{TIERS[i]}">{TIERS[i][0].upper()}</span>some-agent-{i}
    </div>
  </div>""" for i in range(6))

HTML = f"""
<div class="overlay">
  <div class="settings wide gallery-modal">
    <h3>presented documents</h3>
    <label class="checkline gallery-showretired">
      <input type="checkbox" checked /> show retired agents<span class="dim"> · 12</span>
    </label>
    <div class="mailpane">
      <div class="mailer">
        <div class="mailer-list" id="list">{ROWS}</div>
        <div class="mailer-read" id="read">
          <div class="mailer-head" id="head">
            <b>a presented document with a fairly long title 1</b>
            <span class="tier t-sonnet">S</span>
            <span class="dim">some-agent-1</span>
            <span class="dim">this agent has been retired</span>
            <span class="dim">2026-09-03T09:12:44.001Z</span>
            <span class="spacer"></span>
            <button class="dim" id="dismiss">dismiss</button>
          </div>
          <div class="mailer-body md" id="body">
            <h1>A plan</h1>
            {"<p>body paragraph that exists to make the document taller than the pane.</p>" * 40}
          </div>
        </div>
      </div>
    </div>
  </div>
</div>
"""


def failures(page, width: int) -> list[str]:
    return page.evaluate("""(width) => {
      const bad = [];
      const list = document.querySelector('#list');
      const read = document.querySelector('#read');
      const mailer = document.querySelector('.mailer');
      const modal = document.querySelector('.settings');
      const head = document.querySelector('#head');
      const lr = list.getBoundingClientRect();
      const rr = read.getBoundingClientRect();
      const mr = mailer.getBoundingClientRect();
      const dr = modal.getBoundingClientRect();

      // 1. the master-detail split the user asked for: list LEFT, viewer RIGHT
      if (!(lr.right <= rr.left + 0.5))
        bad.push(`list is not left of the viewer (list right ${lr.right}, read left ${rr.left})`);
      if (Math.abs(lr.top - rr.top) > 1)
        bad.push('list and viewer are not side by side (stacked)');
      if (lr.width < 120) bad.push(`entry list collapsed to ${lr.width}px`);
      if (rr.width < 200) bad.push(`document viewer collapsed to ${rr.width}px`);

      // 2. the viewer SCROLLS (the user said "scrollable document viewer") —
      //    the tall body must overflow the pane and be scrollable in place,
      //    not stretch the modal down the screen
      if (read.scrollHeight <= read.clientHeight + 1)
        bad.push('viewer does not overflow — nothing to scroll, body stretched the pane');
      if (getComputedStyle(read).overflowY !== 'auto')
        bad.push(`viewer overflow-y is ${getComputedStyle(read).overflowY}, not auto`);
      if (dr.height > window.innerHeight + 1)
        bad.push(`modal ${dr.height}px is taller than the ${window.innerHeight}px viewport`);
      if (modal.scrollWidth > modal.clientWidth + 1)
        bad.push('panel scrolls horizontally');

      // 3. the entry list scrolls independently of the viewer
      if (getComputedStyle(list).overflowY !== 'auto')
        bad.push('entry list is not independently scrollable');

      // 4. each row actually carries title, agent and time (user's three)
      for (const row of document.querySelectorAll('.mailrow')) {
        const t = row.querySelector('.l1 .mfrom');
        const when = row.querySelector('.l1 .mtime');
        const who = row.querySelector('.l2');
        if (!t || !when || !who) { bad.push('row missing title/time/agent'); continue; }
        const tr = t.getBoundingClientRect(), wr = when.getBoundingClientRect();
        if (wr.left < tr.right - 0.5) bad.push('row time overlaps the title');
        if (t.scrollWidth > t.clientWidth + 1
            && getComputedStyle(t).textOverflow !== 'ellipsis')
          bad.push('a long title is clipped without an ellipsis');
        if (row.getBoundingClientRect().right > lr.right + 0.5)
          bad.push('a row spills out of the entry list');
      }

      // 4b. ACTIVE vs RETIRED read as two steps of ONE ladder, not two
      //     languages. Active wears unread mail's accent; retired is only
      //     slightly quieter and must stay READABLE (this is the check the
      //     "grey them out slightly" instruction can silently overshoot).
      const lum = (c) => {
        const [r, g, b] = c.match(/[\\d.]+/g).slice(0, 3).map(Number);
        const f = (v) => { v /= 255; return v <= .03928 ? v / 12.92
          : Math.pow((v + .055) / 1.055, 2.4); };
        return .2126 * f(r) + .7152 * f(g) + .0722 * f(b);
      };
      const ratio = (a, b) => {
        const [x, y] = [lum(a), lum(b)].sort((m, n) => n - m);
        return (x + .05) / (y + .05);
      };
      const bg = getComputedStyle(document.querySelector('.mailer')).backgroundColor;
      const activeTitle = document.querySelector('.mailrow.active .mfrom');
      const pastTitle = document.querySelector('.mailrow.past .mfrom');
      const aCol = getComputedStyle(activeTitle).color;
      const pCol = getComputedStyle(pastTitle).color;
      if (aCol === pCol)
        bad.push('active and retired titles are the same colour — no distinction at all');
      const pastRatio = ratio(pCol, bg);
      if (pastRatio < 4.5)
        bad.push(`retired title contrast ${pastRatio.toFixed(2)}:1 is below 4.5:1 — `
          + 'greyed past readable, not "slightly"');
      // the active flare must be the ACCENT the unread rule uses, not some
      // other orange picked by hand
      const probe = document.createElement('span');
      probe.style.color = 'var(--accent)';
      document.body.appendChild(probe);
      const accent = getComputedStyle(probe).color;
      probe.remove();
      if (aCol !== accent)
        bad.push(`active title is ${aCol}, not the shared --accent ${accent}`);
      // and retired must not be so faint it reads as disabled
      if (getComputedStyle(document.querySelector('.mailrow.past')).opacity !== '1')
        bad.push('retired row uses opacity — it should be a colour step, not a fade');

      // 4c. every row carries its model chip, on the agent line
      for (const r2 of document.querySelectorAll('.mailrow')) {
        const chip = r2.querySelector('.tier');
        if (!chip) { bad.push('row missing its model chip'); continue; }
        const cr = chip.getBoundingClientRect();
        if (cr.width < 6 || cr.height < 6) bad.push('model chip collapsed');
        if (cr.right > r2.getBoundingClientRect().right + .5)
          bad.push('model chip spills out of its row');
      }

      // 5. the dismiss control the user asked for is IN the viewer, reachable
      const btn = document.querySelector('#dismiss').getBoundingClientRect();
      if (btn.width < 1 || btn.height < 1) bad.push('dismiss button not rendered');
      if (btn.left < rr.left - 0.5 || btn.right > rr.right + 0.5)
        bad.push('dismiss button is outside the viewer pane');
      if (btn.top < rr.top - 0.5) bad.push('dismiss button sits above the viewer');
      // it must stay visible: the head is the pane's own sticky-ish top, so at
      // minimum it may not be pushed off the panel entirely
      if (head.getBoundingClientRect().bottom > dr.bottom + 0.5)
        bad.push('viewer header is pushed off the bottom of the panel');
      return bad.map((v) => `${width}px: ${v}`);
    }""", width)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--expect-fail", action="store_true")
    ap.add_argument("--shot")
    args = ap.parse_args()
    css = CSS.read_text(encoding="utf-8") + (MUTANT if args.expect_fail else "")
    all_failures: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        for width in (1400, 1100, 900):
            page = browser.new_page(viewport={"width": width, "height": 900})
            page.set_content(f"<style>{css}\nbody{{margin:0}}</style>{HTML}")
            all_failures.extend(failures(page, width))
            if args.shot and width == 1400:
                page.screenshot(path=args.shot)
            page.close()
        browser.close()
    if args.expect_fail:
        if not all_failures:
            print("CONTROL FAILED — planted non-scrolling-viewer mutant escaped")
            return 1
        print("CONTROL OK — planted mutant detected:")
        print("\n".join(all_failures[:5]))
        return 0
    if all_failures:
        print("\n".join("FAIL: " + item for item in all_failures))
        return 1
    print("OK — list left / scrollable viewer right, rows carry title+agent+time, "
          "dismiss inside the viewer")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
