"""Real-browser verification of fixed non-provider color for Working state vs provider-themed Active turn.

    python -B frontend/tests/workingstate_color_probe.py
    python -B frontend/tests/workingstate_color_probe.py --expect-fail

Verifies:
1. Out-of-turn Working state receives fixed non-provider color var(--work) (rgb(124, 192, 255))
   across all providers (Claude, OpenAI, Google, OpenRouter) and all surfaces:
   - Zoom card (.sq-idle.working)
   - Desk header (.turn-status-banner.working)
   - Bottom-left tray (.tray-status-label.working)
2. Active in-turn state receives the provider theme (var(--accent))
   across all providers and surfaces:
   - Claude: rgb(217, 119, 87)
   - OpenAI: rgb(34, 196, 189)
   - Google: rgb(80, 144, 245)
   - OpenRouter: rgb(118, 36, 244)
3. Known-negative control (--expect-fail) restores pre-fix CSS where Working used var(--accent)
   and verifies detection of provider leak on non-active Working nodes.
"""
import argparse
import pathlib
import sys

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CSS_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "styles.css"

# Expected colors matching styles.css tokens
COLOR_WORK = "rgb(124, 192, 255)"        # --work (#7cc0ff)
COLOR_CLAUDE = "rgb(217, 119, 87)"       # --accent default (#d97757)
COLOR_OPENAI = "rgb(34, 196, 189)"       # --prov-openai (#22c4bd)
COLOR_GOOGLE = "rgb(80, 144, 245)"       # --prov-google (#5090f5)
COLOR_OPENROUTER = "rgb(118, 36, 244)"   # --prov-openrouter (hsl(263.7 90.4% 54.9%))

PROVIDERS = {
    "claude": {"cls": "", "active_color": COLOR_CLAUDE},
    "openai": {"cls": "prov-openai", "active_color": COLOR_OPENAI},
    "google": {"cls": "prov-google", "active_color": COLOR_GOOGLE},
    "openrouter": {"cls": "prov-openrouter", "active_color": COLOR_OPENROUTER},
}

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
{css}
</style>
</head>
<body style="background: #1e1e1e; color: #fff; padding: 20px;">
{content}
</body>
</html>
"""

def generate_markup() -> str:
    parts = []
    for prov_key, info in PROVIDERS.items():
        pcls = info["cls"]
        desk_pcls = f"desk-body {pcls}" if pcls else "desk-body"
        tray_pcls = f"tray-row {pcls}" if pcls else "tray-row"
        card_pcls = f"sq {pcls}" if pcls else "sq"

        # Out-of-turn Working
        parts.append(f"""
        <!-- {prov_key} out-of-turn working -->
        <div class="test-group" data-provider="{prov_key}" data-mode="working">
          <div class="{card_pcls}">
            <div class="sq-workstate">
              <span class="sq-idle working" id="{prov_key}-card-working">Working</span>
            </div>
          </div>
          <div class="{desk_pcls}">
            <span class="turn-status-banner working" id="{prov_key}-banner-working">
              <span class="turn-status-label">Working</span>
            </span>
          </div>
          <div class="{tray_pcls}">
            <span class="tray-status">
              <span class="tray-status-label working" id="{prov_key}-tray-working">Working</span>
            </span>
          </div>
        </div>
        """)

        # Active in-turn Active
        parts.append(f"""
        <!-- {prov_key} active in-turn -->
        <div class="test-group" data-provider="{prov_key}" data-mode="active">
          <div class="{card_pcls} busy">
            <div class="sq-workstate">
              <span class="sq-idle working active" id="{prov_key}-card-active">Active</span>
            </div>
          </div>
          <div class="{desk_pcls}">
            <span class="turn-status-banner working active" id="{prov_key}-banner-active">
              <span class="turn-status-label">Active</span>
            </span>
          </div>
          <div class="{tray_pcls}">
            <span class="tray-status">
              <span class="tray-status-label working active" id="{prov_key}-tray-active">Active</span>
            </span>
          </div>
        </div>
        """)
    return "\n".join(parts)


PRE_FIX_REGRESSION_CSS = """
/* Known-negative control: restore pre-fix styling where Working took provider --accent */
.sq-idle.working { color: var(--accent) !important; }
.turn-status-banner.working { color: var(--accent) !important; }
.tray-status-label.working { color: var(--accent) !important; }
"""


def measure_colors(css: str) -> dict[str, str]:
    content = generate_markup()
    full_html = HTML_TEMPLATE.format(css=css, content=content)
    results = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page()
        page.set_content(full_html)
        for prov_key in PROVIDERS:
            for mode in ("working", "active"):
                for surface in ("card", "banner", "tray"):
                    elem_id = f"#{prov_key}-{surface}-{mode}"
                    color = page.eval_on_selector(elem_id, "el => getComputedStyle(el).color")
                    results[f"{prov_key}_{surface}_{mode}"] = color
        browser.close()
    return results


def evaluate_results(results: dict[str, str]) -> list[str]:
    failures = []
    for prov_key, info in PROVIDERS.items():
        # 1. Out-of-turn Working must be fixed COLOR_WORK (sky #7cc0ff) across all providers
        for surface in ("card", "banner", "tray"):
            key = f"{prov_key}_{surface}_working"
            actual = results.get(key)
            if actual != COLOR_WORK:
                failures.append(
                    f"Out-of-turn Working on {prov_key} {surface} has color {actual}, expected fixed {COLOR_WORK}"
                )

        # 2. Active in-turn must be the provider's active accent color
        expected_active = info["active_color"]
        for surface in ("card", "banner", "tray"):
            key = f"{prov_key}_{surface}_active"
            actual = results.get(key)
            if actual != expected_active:
                failures.append(
                    f"Active in-turn on {prov_key} {surface} has color {actual}, expected provider {expected_active}"
                )

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expect-fail", action="store_true", help="Run with known-negative pre-fix CSS")
    args = parser.parse_args()

    base_css = CSS_PATH.read_text(encoding="utf-8")
    if args.expect_fail:
        test_css = base_css + "\n" + PRE_FIX_REGRESSION_CSS
    else:
        test_css = base_css

    results = measure_colors(test_css)
    failures = evaluate_results(results)

    if args.expect_fail:
        if not failures:
            print("NEGATIVE CONTROL FAILED: Pre-fix regression was NOT detected!")
            return 1
        print(f"NEGATIVE CONTROL PASSED: Pre-fix regression correctly detected ({len(failures)} failure(s)):")
        for f in failures[:5]:
            print(f"  - {f}")
        if len(failures) > 5:
            print(f"  ... and {len(failures) - 5} more")
        return 0

    if failures:
        print("FAILURES DETECTED:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("ALL 24 BROWSER CHECKS PASSED:")
    print("  - Out-of-turn Working is fixed rgb(124, 192, 255) across Claude, OpenAI, Google, OpenRouter on Card, Banner, Tray (12 checks)")
    print("  - Active in-turn is provider-themed across Claude, OpenAI, Google, OpenRouter on Card, Banner, Tray (12 checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
