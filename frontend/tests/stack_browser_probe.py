import json
from pathlib import Path
from playwright.sync_api import sync_playwright

CSS = Path(__file__).parents[1] / "src" / "styles.css"
HTML = """<!doctype html><style>{}</style>
<div class=overlay id=pin><div class=settings style='position:absolute;left:20px;top:20px;width:220px;height:160px'><button id=pinbtn style='position:absolute;inset:0'>pinned</button></div></div>
<div class=overlay id=plain><div class=settings style='position:absolute;left:20px;top:20px;width:220px;height:160px'><button id=plainbtn style='position:absolute;inset:0'>plain</button></div></div>
<div class=modalpin-over id=nested style='display:none'><div class=overlay><div class=settings style='position:absolute;left:20px;top:20px;width:220px;height:160px'><button id=nestedbtn style='position:absolute;inset:0'>nested</button></div></div></div>"""

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 800, "height": 600})
    page.set_content(HTML.format(CSS.read_text(encoding="utf-8")))
    page.eval_on_selector("#pin", "e => e.classList.add('overlay-pinned')")
    page.eval_on_selector("#pin", "e => e.style.zIndex = '29'")
    result = page.evaluate("""() => {
      const plain = document.querySelector('#plainbtn'); let clicks = 0;
      plain.onclick = () => clicks++;
      const firstTarget = document.elementFromPoint(40, 40); firstTarget.click();
      const first = {target:firstTarget.id, clicks};
      // Raising the pinned window within its band must not overtake ordinary overlays.
      document.querySelector('#pin').style.zIndex = '29';
      const raisedTarget = document.elementFromPoint(40, 40); raisedTarget.click();
      const raised = {target:raisedTarget.id, clicks};
      document.querySelector('#nested').style.display = 'block';
      let nestedClicks = 0;
      document.querySelector('#nestedbtn').onclick = () => nestedClicks++;
      const nestedTarget = document.elementFromPoint(40, 40); nestedTarget.click();
      return {first, raised, nestedTarget:nestedTarget.id,
              nestedClicks,
              plainZ:getComputedStyle(document.querySelector('#plain')).zIndex,
              nestedZ:getComputedStyle(document.querySelector('#nested .overlay')).zIndex,
              pinZ:getComputedStyle(document.querySelector('#pin')).zIndex};
    }""")
    assert result["first"] == {"target": "plainbtn", "clicks": 1}, result
    assert result["raised"] == {"target": "plainbtn", "clicks": 2}, result
    assert result["nestedTarget"] == "nestedbtn", result
    assert result["nestedClicks"] == 1, result
    assert result["plainZ"] == "30" and result["nestedZ"] == "31" and result["pinZ"] == "29", result
    page.evaluate("document.querySelector('#nested').remove()")
    old = page.evaluate("""() => {
      document.querySelector('#plain').style.zIndex = '20';
      return {target:document.elementFromPoint(40,40).id,
              plainZ:getComputedStyle(document.querySelector('#plain')).zIndex};
    }""")
    assert old["target"] != "plainbtn" and old["plainZ"] == "20", old
    result["oldZControl"] = old
    page.screenshot(path=str(Path(__file__).parents[2] / "stack-browser-evidence.png"))
    browser.close()
print(json.dumps(result, sort_keys=True))
