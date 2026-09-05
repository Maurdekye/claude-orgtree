"""Which controls can be disabled without ever saying why?

A disabled control is fine when the reason is already on screen. Several here
already do that well — the tools list writes "— parent doesn't hold it" beside
the checkbox, and the model switch appends its own `why` to the option label —
so "the tag has no title=" is a BAD proxy and reports them as defects. This
looks at the enclosing block too, and classifies each site:

  self-evident  the gate is a transient `busy` / an empty field the user is
                looking at — a tooltip would be noise, not help
  explained     a title on the tag or its enclosing element, or explanatory
                text rendered next to it
  UNEXPLAINED   nothing on screen says why. These are the ones to fix.

The classification is a shortlist to READ, not a verdict: a title can be present
and useless. Every site is confirmed by opening the file.

    python audit_disabled.py [--all]
"""
from __future__ import annotations

import pathlib
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path("src")
SHOW_ALL = "--all" in sys.argv

# gates that are their own explanation: the control is dead only while a
# request is in flight, or while the field beside it is empty
SELF_EVIDENT = re.compile(
    r"disabled=\{[^}]*\b("
    r"busy|saving|sending|pending|inflight|"
    r"!\w+\.trim\(\)|!\w+\?\.\w+|"
    r"\w+ *[<>=]=? *\d"          # numeric range ends: dpi <= 0.75, offset === 0
    r")", re.I)


def tags(text: str):
    i = 0
    while True:
        i = text.find("<", i)
        if i < 0:
            return
        if i + 1 >= len(text) or not (text[i + 1].isalpha() or text[i + 1] == "_"):
            i += 1
            continue
        depth, j, instr = 0, i + 1, ""
        while j < len(text):
            c = text[j]
            if instr:
                if c == instr and text[j - 1] != "\\":
                    instr = ""
            elif c in "\"'`":
                instr = c
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            elif c == ">" and depth == 0:
                break
            j += 1
        yield i, text.count("\n", 0, i) + 1, text[i:j + 1]
        i = j + 1


def explained_nearby(src: str, start: int, tag: str) -> str | None:
    """A title on the tag, or on the element that wraps it, or explanatory text
    rendered right after it. Window is deliberately generous — a false
    'explained' costs a re-read, a false 'UNEXPLAINED' costs a pointless edit."""
    if re.search(r"\btitle\s*=", tag):
        return "title on the control"
    before = src[max(0, start - 420):start]
    if re.search(r"\btitle\s*=", before.rsplit("<label", 1)[-1]) \
            or re.search(r"\btitle\s*=", before.rsplit("<div", 1)[-1]):
        return "title on the enclosing element"
    after = src[start:start + 520]
    if re.search(r"(className=\"dim\"|className=\"hint\"|className='dim'"
                 r"|hint\b|&mdash;|—)", after):
        return "explanatory text beside it"
    return None


rows: list[tuple[str, str, int, str]] = []
for path in sorted(ROOT.rglob("*.tsx")):
    src = path.read_text(encoding="utf-8")
    for start, line, tag in tags(src):
        if not re.search(r"\bdisabled\s*=", tag):
            continue
        if SELF_EVIDENT.search(tag):
            kind = "self-evident"
        else:
            why = explained_nearby(src, start, tag)
            kind = f"explained ({why})" if why else "UNEXPLAINED"
        flat = " ".join(tag.split())
        rows.append((kind, str(path), line, flat[:130]))

for k in ("self-evident", "explained", "UNEXPLAINED"):
    n = sum(1 for r in rows if r[0].startswith(k))
    print(f"{n:>3}  {k}")
print()
for kind, path, line, snip in rows:
    if kind == "UNEXPLAINED" or SHOW_ALL:
        print(f"[{kind}] {path}:{line}\n    {snip}\n")
