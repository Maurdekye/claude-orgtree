"""Real-browser desk-header geometry and planted single-row mutant.

    python -B tests/header_layout_probe.py
    python -B tests/header_layout_probe.py --expect-fail
"""
from __future__ import annotations

import argparse
import pathlib

from playwright.sync_api import sync_playwright


CSS = pathlib.Path(__file__).resolve().parents[1] / "src" / "styles.css"
MUTANT = """
.cc-head { flex-direction: row !important; flex-wrap: nowrap !important;
  overflow: hidden !important; }
.cc-head-top, .cc-head-meta { display: contents !important; }
.cc-head * { flex-shrink: 1 !important; }
.cc-name { white-space: nowrap !important; overflow: hidden !important;
  text-overflow: ellipsis !important; }
"""

HTML = """
<div class="desk-body" id="desk">
  <div class="cc-head" id="head">
    <div class="cc-head-top">
      <span class="cc-head-left">
        <span class="tier">F</span>
        <span class="cc-name" title="an-agent-name-long-enough-to-wrap-at-enlarged-browser-text">an-agent-name-long-enough-to-wrap-at-enlarged-browser-text</span>
        <span class="cc-context-seat"><span class="ctxwheel">◔</span></span>
        <span class="cc-process-seat"><span class="proc-state relaunch"><span class="proc-one-mark"></span>↻</span></span>
        <span class="turn-status-banner working"><span class="cc-spin">↻</span><span>Working</span><span class="turn-status-time">31m</span></span>
      </span>
      <span class="spacer"></span>
      <span class="cc-head-right">
        <span class="cc-actions"><button class="danger">dissolve · 30</button></span>
        <span class="cc-tabs"><button>chat</button><button>history</button><button>files</button><button>inbox 12</button></span>
        <button class="cc-icon" aria-label="settings">⚙</button>
      </span>
    </div>
    <div class="cc-head-meta">
      <span class="mcp-tool-count changed">MCP 127</span>
      <span class="cache-forecast cold">cache ×</span>
      <span class="statuschip working">working</span>
      <span class="badge frozen">usage halted until a deliberately long reset explanation</span>
      <span class="badge free">audience-with-a-very-long-name-that-cannot-be-clipped</span>
      <span class="badge free">user</span><span class="badge free">org inbox</span>
      <span class="badge">$123.45</span><span class="badge">12 queued</span>
      <span class="badge">fallback 4 · safe-public-label</span>
    </div>
  </div>
  <div class="desk-nav" id="superior"><button class="desk-nav-chip">↑ superior</button></div>
  <div id="transcript" style="min-height:120px;border:1px solid transparent">conversation</div>
  <div class="cache-send-warning miss">⚠ Cache miss expected — this session is incompatible and automatic cheap compaction will not run.</div>
  <div class="cc-composer"><textarea>message</textarea><button class="cc-send">↑</button></div>
</div>
"""


def failures(page, width: int, enlarged: bool) -> list[str]:
    return page.evaluate("""({width, enlarged}) => {
      const head = document.querySelector('#head');
      const desk = document.querySelector('#desk');
      const top = document.querySelector('.cc-head-top');
      const meta = document.querySelector('.cc-head-meta');
      const left = document.querySelector('.cc-head-left');
      const right = document.querySelector('.cc-head-right');
      const superior = document.querySelector('#superior');
      const transcript = document.querySelector('#transcript');
      const hr = head.getBoundingClientRect();
      const dr = desk.getBoundingClientRect();
      const tr = transcript.getBoundingClientRect();
      const bad = [];
      if (head.scrollWidth > head.clientWidth + 1) bad.push('header horizontal overflow');
      if (desk.scrollWidth > desk.clientWidth + 1) bad.push('desk horizontal overflow');
      if (hr.bottom > tr.top + .5) bad.push('header overlays transcript');
      if (meta.getBoundingClientRect().top < top.getBoundingClientRect().bottom - .5)
        bad.push('metadata overlaps bounded row');
      if (superior.getBoundingClientRect().top < meta.getBoundingClientRect().bottom - .5)
        bad.push('superior strip overlaps metadata row');
      for (const el of head.querySelectorAll('button,.badge,.mcp-tool-count,.cache-forecast,.proc-state')) {
        const r = el.getBoundingClientRect();
        const scrollParent = el.closest('.cc-head-right');
        const safelyScrollable = scrollParent
          && getComputedStyle(scrollParent).overflowX === 'auto';
        if (!safelyScrollable && (r.left < dr.left - .5 || r.right > dr.right + .5))
          bad.push(`${el.className || el.textContent}: offscreen`);
      }
      for (const el of top.querySelectorAll('button')) {
        if (el.getBoundingClientRect().height < 23.5)
          bad.push(`${el.textContent}: shrunken hit target`);
      }
      for (const sel of ['.cc-context-seat', '.cc-process-seat']) {
        const r = top.querySelector(sel).getBoundingClientRect();
        if (r.width < 27.5 || r.height < 23.5)
          bad.push(`${sel}: unstable static slot`);
      }
      if (top.querySelector('.turn-status-banner').getBoundingClientRect().width < 71.5)
        bad.push('status/age banner collapsed');
      if (meta.querySelector('.ctxwheel,.proc-state,.turn-status-banner'))
        bad.push('static top-row item duplicated in metadata');
      const cluster = left;
      const expected = ['tier', 'cc-name', 'cc-context-seat', 'cc-process-seat',
        'turn-status-banner'];
      const actual = [...cluster.children].map((el) => el.classList[0]);
      if (actual.join('|') !== expected.join('|')) bad.push('information order changed');
      const topOrder = [...top.children].map((el) => el.classList[0]);
      if (topOrder.join('|') !== 'cc-head-left|spacer|cc-head-right')
        bad.push('top group/spacer order changed');
      const rightOrder = [...right.children].map((el) => el.classList[0]);
      if (rightOrder.join('|') !== 'cc-actions|cc-tabs|cc-icon')
        bad.push('right action order changed');
      const items = [...cluster.children];
      for (let i = 1; i < items.length; i++) {
        const before = items[i - 1].getBoundingClientRect();
        const after = items[i].getBoundingClientRect();
        if (Math.abs(before.top - after.top) < .5 && after.left - before.right > 2.5)
          bad.push(`information gap ${after.left - before.right}px exceeds compact token`);
      }
      const leftRows = new Set([...left.children].map((el) => {
        const r = el.getBoundingClientRect();
        return Math.round((r.top + r.height / 2) * 2) / 2;
      }));
      if (leftRows.size !== 1) bad.push('left group wrapped internally');
      const rightRows = new Set([...right.children].map((el) => {
        const r = el.getBoundingClientRect();
        return Math.round((r.top + r.height / 2) * 2) / 2;
      }));
      if (rightRows.size !== 1) bad.push('right group wrapped internally');
      if (width === 951) {
        const lr = left.getBoundingClientRect(), rr = right.getBoundingClientRect();
        if (Math.abs((lr.top + lr.height / 2) - (rr.top + rr.height / 2)) > .5)
          bad.push('951px groups did not stay on one line');
        if (top.getBoundingClientRect().height > Math.max(lr.height, rr.height) + 1)
          bad.push('951px top row gained a stranded second line');
      }
      if (Math.abs(right.getBoundingClientRect().right - top.getBoundingClientRect().right) > .5)
        bad.push('right group is not right aligned');
      const name = document.querySelector('.cc-name');
      if (name.scrollWidth > name.clientWidth + 1 && !name.getAttribute('title'))
        bad.push('clipped agent name has no accessible full label');
      return bad.map((v) => `${width}px${enlarged ? ' enlarged' : ''}: ${v}`);
    }""", {"width": width, "enlarged": enlarged})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--expect-fail", action="store_true")
    args = ap.parse_args()
    css = CSS.read_text(encoding="utf-8") + (MUTANT if args.expect_fail else "")
    all_failures: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        for width, enlarged in [(951, False), (900, False), (420, False), (320, False),
                                (420, True), (320, True)]:
            page = browser.new_page(viewport={"width": width, "height": 900})
            size = "20px" if enlarged else "13px"
            page.set_content(
                f"<style>{css}\nbody{{margin:0}} #desk{{width:100%;font-size:{size}}}"
                f"#desk button,#desk .cc-name,#desk .badge,#desk .turn-status-banner{{font-size:inherit}}</style>{HTML}")
            all_failures.extend(failures(page, width, enlarged))
            page.close()
        browser.close()
    if args.expect_fail:
        if not all_failures:
            print("CONTROL FAILED — planted single-row shrink/clip mutant escaped")
            return 1
        print("CONTROL OK — planted single-row mutant detected:")
        print("\n".join(all_failures[:5]))
        return 0
    if all_failures:
        print("\n".join("FAIL: " + item for item in all_failures))
        return 1
    print("OK — two-row header stays reachable at desktop/mobile widths and enlarged text")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
