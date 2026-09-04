"""Charter presets - the header must never become charter text.

    python backend/tests/test_charter_presets.py     (no pytest; plain asserts)

WHY THIS SUITE EXISTS

`docs/charters/*.md` are presets for the manual hire form. Per DECISIONS.md
D-057 a preset may open with a human-facing HEADER that ends at a `---` line,
and **only what follows becomes charter text**. The header is written AT THE
USER ("Paste this into the charter field of a single top-level agent") and is
nonsense as an instruction to the agent. `cards.tsx`'s `finalCharter()` uses
the served string verbatim, so anything that leaks past that split is what the
new hire is told it is.

⚠ THE SPLIT IS CORRECT, AND IT IS CORRECT FOR AN INVISIBLE REASON.
`charters_list` matches `"\n---\n"`. Every preset here is CRLF on disk, so the
separator line is `\r\n---\r\n` and that pattern cannot match those bytes -
except the file is read in TEXT MODE, whose universal-newline translation has
already turned every `\r\n` into `\n` before the split ever runs. Nothing at
the call site says so. Change that read to binary, or pass `newline=""`, and
the split silently stops matching: `[-1]` then returns the WHOLE FILE and the
header ships as charter text, with no error and no truncation. Confirmed by
mutation 2026-09-04 - `newline=""` alone turns §1's CRLF and mixed checks red.

That is the whole reason this file exists. It is also worth recording that on
2026-09-04 the same expression was measured at the BYTE level, off the
endpoint, and pronounced broken; the endpoint disproved it in one call. Every
check here therefore goes through the ENDPOINT (`api.charters_list`, the
function FastAPI calls for GET /api/charters) and never through whatever
internal helper does the splitting.

    §1 runs the endpoint against a FIXTURE directory this file writes, so the
       CRLF case is checked whether or not any shipped file happens to be CRLF
       in your checkout. This is the part that cannot go vacuous.
    §2 runs it against the REAL presets. It is checkout-dependent - it can
       only catch a CRLF regression while a preset file is actually CRLF on
       disk - so it says which files it is standing on, out loud.

⚠ §2 ALSO GUARDS AGAINST A BOM IN THE MIDDLE OF A FILE, which is not a
theoretical worry: commit a595353 (2026-09-04) put a UTF-8 BOM at the head of
coordinator.md's body, because the draft it was assembled from had been
written by PowerShell `Set-Content -Encoding utf8`. The served charter then
opened with U+FEFF. `str.strip()` does not remove it - U+FEFF is not
whitespace - and nothing else looked. §1's `bom` fixture is the positive
control for that check: it proves the endpoint passes a BOM through, so §2's
absence assertion is standing on something.
"""

import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

# an isolated data root BEFORE any orgtree import: store resolves ORGTREE_DATA
# at import time, and importing api imports store. mkdtemp also puts the root
# under the OS temp dir, which is what keeps net._default_address off the
# operator's real mail hub (see test_external_mail §1).
os.environ["ORGTREE_DATA"] = tempfile.mkdtemp(prefix="orgtree-charters-")

from orgtree import api                                        # noqa: E402

PASS = 0


def check(label, fn):
    global PASS
    fn()
    PASS += 1
    print(f"  ok {PASS:3d}  {label}")


def t(label):
    def deco(fn):
        check(label, fn)
        return fn
    return deco


def payload(dirpath):
    """GET /api/charters against `dirpath` — the whole payload."""
    old = api.CHARTERS_DIR
    api.CHARTERS_DIR = dirpath
    try:
        out = api.charters_list()
    finally:
        api.CHARTERS_DIR = old
    assert isinstance(out, dict) and "charters" in out, out
    return out


def records(dirpath):
    """GET /api/charters against `dirpath`, as {name: full record}."""
    return {c["name"]: c for c in payload(dirpath)["charters"]}


def charters(dirpath):
    """GET /api/charters against `dirpath`, as {name: content}."""
    return {k: v["content"] for k, v in records(dirpath).items()}


HEADER = "Paste this into the charter field of a single top-level agent"
BODY = "You are the FIXTURE - the marker line that must survive"
BOM = "\ufeff"

FIX = tempfile.mkdtemp(prefix="orgtree-charterfix-")


def write(name, text):
    with open(os.path.join(FIX, name), "wb") as f:
        f.write(text.encode("utf-8"))


# ============================================================================ §1
print("\n§1 the split, against fixtures this file controls")

# One logical document, three line-ending regimes. The body marker must
# survive all three; the header marker must survive none.
DOC = ("# The Fixture charter\n\n" + HEADER + ".\n\n---\n\n"
       + BODY + "\n\nrule two.\n")
write("crlf.md", DOC.replace("\n", "\r\n"))
write("lf.md", DOC)
# mixed: a CRLF header (how a Windows editor leaves it) with an LF body
_h, _b = DOC.split("\n---\n", 1)
write("mixed.md", _h.replace("\n", "\r\n") + "\r\n---\r\n" + _b)
write("nosep.md", (BODY + "\n\nno separator anywhere in this file.\n")
      .replace("\n", "\r\n"))
write("late.md", ("header line\n\n---\n\n" + BODY + "\n\n---\n\nstill body.\n")
      .replace("\n", "\r\n"))
write("big.md", ("x\n\n---\n\n" + "y" * 7000 + "\n").replace("\n", "\r\n"))
# the positive control for §2's BOM check - a BOM at the head of the BODY,
# which is exactly what a595353 shipped
write("bom.md", ("x\n\n---\n\n" + BOM + BODY + "\n").replace("\n", "\r\n"))

RECS = records(FIX)
GOT = {k: v["content"] for k, v in RECS.items()}


@t("a CRLF preset does not serve its header")
def _crlf():
    c = GOT["crlf"]
    assert HEADER not in c, \
        f"CRLF preset served its header ({len(c)} chars): {c[:120]!r}"
    assert "# The Fixture charter" not in c, f"served the title: {c[:120]!r}"
    assert BODY in c, f"CRLF preset lost its body: {c[:200]!r}"
    assert c.startswith(BODY), f"body did not start at the separator: {c[:80]!r}"


@t("an LF preset does not serve its header")
def _lf():
    c = GOT["lf"]
    assert HEADER not in c, f"LF preset served its header: {c[:120]!r}"
    assert BODY in c and c.startswith(BODY), c[:200]


@t("a mixed CRLF-header / LF-body preset does not serve its header")
def _mixed():
    c = GOT["mixed"]
    assert HEADER not in c, f"mixed preset served its header: {c[:120]!r}"
    assert BODY in c, c[:200]


@t("a preset with NO separator still serves its whole content")
def _nosep():
    c = GOT["nosep"]
    assert BODY in c, f"no-separator preset served nothing useful: {c!r}"
    assert "no separator anywhere" in c, c


@t("only the FIRST separator splits - a later --- stays in the body")
def _late():
    c = GOT["late"]
    assert "header line" not in c, f"served the header: {c[:120]!r}"
    assert BODY in c and "still body" in c, c
    assert "---" in c, "the body's own horizontal rule was eaten"


@t("the 6000-char cut still happens - and is now DECLARED, not silent")
def _trunc():
    # This check used to read "the 6000-char truncation is unchanged" and
    # assert only len == 6000. The cut is still here (the endpoint serves
    # whatever .md files exist, so the cap stays), but a cut that says nothing
    # is the defect: the hire form offered a card whose text simply stopped.
    # Now the record must CARRY the true length and admit it was cut.
    r = RECS["big"]
    c = r["content"]
    assert len(c) == api.PRESET_MAX, \
        f"expected the {api.PRESET_MAX} cap, got {len(c)}"
    assert c.startswith("y"), f"truncated the wrong end: {c[:40]!r}"
    assert r.get("truncated") is True, \
        f"a cut body did not report truncated=True: {r.get('truncated')!r}"
    assert r.get("chars") == 7000, (
        "the record must carry the body's TRUE length (7000, the fixture's "
        f"'y' * 7000) so the UI can say what was lost; got {r.get('chars')!r}")
    assert r["chars"] > len(c), "chars must be the pre-cut length, not the cut one"


@t("POSITIVE CONTROL: an UNCUT preset reports truncated=False and a true length")
def _not_trunc():
    # Without this, `truncated` could be hardcoded True and _trunc would still
    # pass - the flag would mean nothing. This proves the flag DISCRIMINATES.
    r = RECS["crlf"]
    assert r.get("truncated") is False, \
        f"a short body claimed it was truncated: {r.get('truncated')!r}"
    assert r.get("chars") == len(r["content"]), (
        "for an uncut body chars must equal the served length: "
        f"{r.get('chars')} vs {len(r['content'])}")


@t("the payload states BOTH limits, so no client has to hardcode them")
def _limits():
    p = payload(FIX)
    assert p.get("preset_max") == api.PRESET_MAX, p.get("preset_max")
    # the charter EDIT limit - a preset can be under preset_max and still be
    # over this, which is the case the hire form has to warn about
    from orgtree import ledger as _lg
    assert p.get("charter_max") == _lg.CHARTER_MAX, p.get("charter_max")
    assert p["charter_max"] < p["preset_max"], (
        "charter_max is expected to be the TIGHTER of the two - if that ever "
        "stops being true, the hire form's warning logic needs revisiting")


@t("POSITIVE CONTROL: a BOM in a body IS served - so §2's check can fire")
def _bom_passes_through():
    c = GOT["bom"]
    assert BOM in c, (
        "the endpoint stripped U+FEFF, so §2's 'no BOM in a shipped preset' "
        "check can no longer fail and is worthless as written - rewrite it "
        "or delete it, do not leave it standing")
    assert c.startswith(BOM), f"BOM did not survive at the head: {c[:20]!r}"


@t("every fixture came back, and each one non-empty")
def _all():
    for n in ("crlf", "lf", "mixed", "nosep", "late", "big", "bom"):
        assert n in GOT, f"{n} missing from the response: {sorted(GOT)}"
        assert GOT[n].strip(), f"{n} served an empty charter"


# ============================================================================ §2
print("\n§2 the shipped presets")

REAL = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "docs", "charters"))
SERVED_RECS = records(REAL)
SERVED = {k: v["content"] for k, v in SERVED_RECS.items()}
FILES = sorted(f for f in os.listdir(REAL) if f.endswith(".md"))
CRLF_ON_DISK = [f for f in FILES
                if open(os.path.join(REAL, f), "rb").read().count(b"\r\n")]


@t("every preset file on disk is served")
def _served():
    assert FILES, f"no presets found under {REAL}"
    assert len(SERVED) == len(FILES), (sorted(SERVED), FILES)


def _clean_body(fn):
    def go():
        c = SERVED[fn[:-3].replace("-", " ")]
        raw = open(os.path.join(REAL, fn), encoding="utf-8").read()
        title = raw.splitlines()[0].strip()
        assert c.strip(), f"{fn} served an empty charter"
        assert HEADER not in c, \
            f"{fn} served the user-facing header line ({len(c)} chars)"
        if title.startswith("#"):
            assert title not in c, \
                f"{fn} served its title {title!r} - the header reached the body"
        assert BOM not in c, (
            f"{fn} serves a U+FEFF at offset {c.find(BOM)} of its charter "
            f"text: {c[max(0, c.find(BOM) - 20):c.find(BOM) + 20]!r}. A BOM in "
            "the middle of a file is invisible in every editor and survives "
            "str.strip(); it is what PowerShell Set-Content -Encoding utf8 "
            "leaves behind when a file is assembled from another one.")
        # ask the ENDPOINT whether it cut this file, rather than re-deriving
        # the cap here — a length check would go stale the moment the cap moves
        assert SERVED_RECS[fn[:-3].replace("-", " ")]["truncated"] is False, \
            f"{fn} is {len(c)} chars and is being cut at {api.PRESET_MAX}"
    return go


for _f in FILES:
    check(f"{_f} serves a clean body only", _clean_body(_f))


@t("served lengths, recorded - against BOTH limits")
def _lengths():
    from orgtree import ledger as _lg
    over = []
    for f in FILES:
        c = SERVED[f[:-3].replace("-", " ")]
        raw = os.path.getsize(os.path.join(REAL, f))
        flag = "  << over charter_max" if len(c) > _lg.CHARTER_MAX else ""
        if flag:
            over.append(f)
        print(f"        {f:16s} file {raw:6d} B   served {len(c):5d} chars"
              f"   cut headroom {api.PRESET_MAX - len(c):5d}"
              f"   edit headroom {_lg.CHARTER_MAX - len(c):6d}{flag}")
    if over:
        # NOT a failure: hire() does not enforce CHARTER_MAX, so these are
        # hireable. They just cannot be EDITED later without shortening, and
        # the hire form now says so on the card.
        print(f"\n        note: {over} exceed the {_lg.CHARTER_MAX}-char "
              "charter EDIT limit. Hiring works; a later charter edit will "
              "refuse until shortened. The draft card warns about this.")


if not CRLF_ON_DISK:
    print("\n  ! §2 IS INERT FOR THE CRLF CASE IN THIS CHECKOUT: none of "
          f"{FILES} has CRLF line endings on disk, so these checks would pass "
          "even against a split that cannot handle CRLF. §1 covers it.")
else:
    print(f"\n  §2 is live for the CRLF case: {CRLF_ON_DISK} are CRLF on disk.")

print(f"\nALL {PASS} CHECKS PASS")
