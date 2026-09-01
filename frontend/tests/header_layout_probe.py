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
      <span class="tier">F</span>
      <span class="cc-name">an-agent-name-long-enough-to-wrap-at-enlarged-browser-text</span>
      <span class="cc-turn-seat"><span class="cc-working">↻ working · 31m · 8 tasks</span></span>
      <span class="cc-context-seat"><span class="ctxwheel">◔</span></span>
      <span class="cc-process-seat"><span class="proc-state relaunch"><span class="proc-one-mark"></span>↻</span></span>
      <span class="spacer"></span>
      <span class="cc-actions"><button class="danger">dissolve · 30</button></span>
      <span class="cc-tabs"><button>chat</button><button>history</button><button>files</button><button>inbox 12</button></span>
      <button class="cc-icon" aria-label="settings">⚙</button>
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
      for (const el of head.querySelectorAll('button,.badge,.mcp-tool-count,.cache-forecast,.proc-state')) {
        const r = el.getBoundingClientRect();
        if (r.left < dr.left - .5 || r.right > dr.right + .5)
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
      if (top.querySelector('.cc-turn-seat').getBoundingClientRect().width < 51.5)
        bad.push('turn age/activity seat collapsed');
      if (meta.querySelector('.ctxwheel,.proc-state,.cc-working'))
        bad.push('static top-row item duplicated in metadata');
      const name = document.querySelector('.cc-name');
      if (name.scrollWidth > name.clientWidth + 1) bad.push('agent name clipped');
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
                f"#desk button,#desk .cc-name,#desk .badge,#desk .cc-working{{font-size:inherit}}</style>{HTML}")
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
