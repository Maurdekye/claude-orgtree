"""Charter length - text must never be lost, and charters are NOT capped.

    python backend/tests/test_charter_length.py     (no pytest; plain asserts)
    python backend/tests/test_charter_length.py --discriminate

WHY THIS SUITE EXISTS

`set_scope` used to store charters as `charter.strip()[:4000]`. Over-long text
was cut mid-word and NOTHING said so - not the return value, not a warning, not
the UI. This org's own shipped team charter had been ending mid-sentence for an
unknown period and no agent ever read its final rule.

⚠ THE CAP IS GONE. User ruling 2026-09-04, verbatim: "uncap it." There is no
maximum charter length, no refusal, and no truncation. An earlier revision of
this suite asserted a REFUSAL above 4000; those checks were rewritten, not
deleted, because the valuable assertion was never "it refuses" - it was
NOTHING IS SILENTLY LOST, and that survives uncapping intact. What replaced
them asserts the stronger property: whatever you write is what gets stored,
byte for byte, at any length.

`CHARTER_LONG` remains, as an ADVISORY THRESHOLD only. Above it the ledger
notes how long the text is, because a charter is re-sent in that agent's system
prompt on EVERY turn - so a long one is a recurring cost, not a one-off. The
note never blocks anything. §3 proves it fires, and proves it stays quiet
otherwise; a notice that fired always would carry no information.

    §1  stored WHOLE and byte-exact, at every length including absurd ones
    §2  the old truncation's fingerprint, checked for directly
    §3  the advisory: fires when long, silent when not
    §4  the cap is really gone - no length is refused, and 4000 is not special

⚠ ON `--discriminate`: three mutations put the old defects back in a TEMP COPY
of the package and require this file to go red. A suite that has never been
seen failing is not evidence of anything.
"""

import json
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

# an isolated data root BEFORE any orgtree import: store resolves ORGTREE_DATA
# at import time, and importing ledger imports store. mkdtemp also puts the root
# under the OS temp dir, which keeps net._default_address off the operator's
# real mail hub (see test_external_mail §1).
os.environ["ORGTREE_DATA"] = tempfile.mkdtemp(prefix="orgtree-charterlen-")
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w",
          encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

from orgtree.ledger import CHARTER_LONG, USER, LedgerError, Org   # noqa: E402

# establish the process CANNOT resolve to the operator's live root
_LIVE = os.path.normpath(os.path.expanduser("~/orgtree"))
_GOT = os.path.normpath(os.environ["ORGTREE_DATA"])
assert _GOT != _LIVE and not _GOT.startswith(_LIVE + os.sep), (_GOT, _LIVE)

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


ALL_TOOLS = dict(bash=True, web=False, edit=True, subagents=False, mcp=[])
SPEC = dict(add_dirs=[], tools=ALL_TOOLS, org_visibility="team",
            charter="seed charter")

TAIL = " ...THE-TAIL-THAT-MUST-SURVIVE"


def body(n, fill="A"):
    """A charter of EXACTLY n characters, ending in a recognisable tail."""
    assert n >= len(TAIL)
    return fill * (n - len(TAIL)) + TAIL


def org_with(charter=None):
    o = Org.create("clen")
    o.hire(USER, None, "haiku", 10, "boss", **SPEC)
    spec = dict(SPEC)
    if charter is not None:
        spec["charter"] = charter
    o.hire("boss", "boss", "haiku", 0, "kid", **spec)
    return o


def snap(o):
    return json.dumps(o.d, sort_keys=True, default=str)


def length_notes(res):
    """Just the charter-length notes out of a set_scope result's warnings."""
    return [w for w in (res.get("warnings") or [])
            if "chars" in w and "charter" in w]


# ============================================================================ §1
print("\n§1 stored WHOLE and byte-exact, at every length")


@t("a charter is stored EXACTLY as written, across four orders of magnitude")
def _exact():
    o = org_with()
    for n in (len(TAIL), 500, CHARTER_LONG - 1, CHARTER_LONG,
              CHARTER_LONG + 1, 10_000, 250_000):
        txt = body(n)
        o.set_scope(USER, "kid", charter=txt)
        got = o.nodes["kid"]["charter"]
        # `.strip()` is the contract, not a dodge: set_scope strips SURROUNDING
        # whitespace (§1's _strip pins that down). It matters only for the
        # smallest case, where the body IS the tail and the tail opens with a
        # space; every other length here starts with fill and is unaffected.
        assert got == txt.strip(), (
            f"length {n}: stored {len(got)} chars, wrote {len(txt.strip())} - "
            f"ends {got[-40:]!r}")


@t("team_charter is stored whole at the same lengths")
def _exact_team():
    o = org_with()
    for n in (CHARTER_LONG + 1, 50_000):
        txt = body(n, fill="T")
        o.set_scope(USER, "kid", team_charter=txt)
        assert o.nodes["kid"]["team_charter"] == txt, n


@t("a very long multibyte charter survives byte for byte")
def _multibyte():
    o = org_with()
    txt = "é" * 20_000 + TAIL
    o.set_scope(USER, "kid", charter=txt)
    got = o.nodes["kid"]["charter"]
    assert got == txt, f"stored {len(got)} chars vs {len(txt)}"
    assert len(got.encode("utf-8")) == len(txt.encode("utf-8")), "byte drift"
    assert got.endswith(TAIL)


@t("clearing a charter with '' still works (empty ⇒ None)")
def _clear():
    o = org_with()
    o.set_scope(USER, "kid", charter="")
    assert o.nodes["kid"]["charter"] is None, o.nodes["kid"]["charter"]


@t("surrounding whitespace is stripped, interior text is untouched")
def _strip():
    o = org_with()
    txt = body(9000)
    o.set_scope(USER, "kid", charter="\n\n  " + txt + "  \n\n")
    assert o.nodes["kid"]["charter"] == txt


# ============================================================================ §2
print("\n§2 the old truncation's fingerprint")


@t("no stored charter ever ends exactly at a round cap with its tail missing")
def _fingerprint():
    # the defect's signature: length lands exactly on a cap AND the known tail
    # is gone. Checked directly rather than inferred, at every historical cap.
    o = org_with()
    for cap in (4000, 6000, CHARTER_LONG):
        txt = body(cap + 1500)
        o.set_scope(USER, "kid", charter=txt)
        got = o.nodes["kid"]["charter"]
        assert len(got) != cap, \
            f"stored length landed exactly on the old {cap} cap: truncated"
        assert got.endswith(TAIL), \
            f"tail lost against cap {cap}: ends {got[-40:]!r}"


@t("POSITIVE CONTROL: this file can actually detect a truncation")
def _fingerprint_control():
    # prove the assertion above is capable of failing, by checking that a
    # deliberately cut string DOES trip the same two conditions. Without this,
    # §2 could be passing because the checks are malformed.
    cut = body(4000 + 1500)[:4000]
    assert len(cut) == 4000 and not cut.endswith(TAIL), \
        "the control string is not actually truncated - §2 proves nothing"


@t("a long charter survives a RELOAD (it is persisted, not just in memory)")
def _reload():
    o = org_with()
    txt = body(30_000)
    o.set_scope(USER, "kid", charter=txt)
    back = Org(json.loads(json.dumps(o.d)))       # what load_org does
    assert back.nodes["kid"]["charter"] == txt, \
        f"reload lost text: {len(back.nodes['kid']['charter'])} vs {len(txt)}"


# ============================================================================ §3
print("\n§3 the advisory - it fires, and it stays quiet")


@t(f"a charter over CHARTER_LONG ({CHARTER_LONG}) is REPORTED, with its length")
def _notes():
    o = org_with()
    n = CHARTER_LONG + 1218
    r = o.set_scope(USER, "kid", charter=body(n))
    notes = length_notes(r)
    assert notes, f"a {n}-char charter was stored SILENTLY: {r.get('warnings')!r}"
    assert str(n) in " ".join(notes), \
        f"the note does not state the measured length: {notes}"


@t("the note says the text was KEPT - it must not read like a refusal")
def _note_wording():
    o = org_with()
    joined = " ".join(length_notes(
        o.set_scope(USER, "kid", charter=body(CHARTER_LONG + 50)))).lower()
    assert "whole" in joined or "stored" in joined, joined
    for banned in ("too long", "not saved", "shorten", "refus"):
        assert banned not in joined, \
            f"the note still reads like the old refusal ({banned!r}): {joined}"


@t("the note explains the PER-TURN cost, which is why it exists at all")
def _note_cost():
    o = org_with()
    joined = " ".join(length_notes(
        o.set_scope(USER, "kid", charter=body(CHARTER_LONG + 50)))).lower()
    assert "every turn" in joined or "per-turn" in joined, joined


@t("POSITIVE CONTROL: a SHORT charter produces no length note at all")
def _quiet():
    # without this, the advisory could fire unconditionally and mean nothing.
    o = org_with()
    r = o.set_scope(USER, "kid", charter=body(200),
                    add_dirs=[{"path": "E:/w", "mode": "rw"}])
    assert not length_notes(r), f"a short charter was flagged: {r['warnings']!r}"


@t("...and exactly AT the threshold is still quiet (boundary discriminates)")
def _boundary():
    o = org_with()
    assert not length_notes(o.set_scope(USER, "kid", charter=body(CHARTER_LONG)))
    assert length_notes(o.set_scope(USER, "kid", charter=body(CHARTER_LONG + 1)))


# ============================================================================ §4
print("\n§4 the cap is really gone")


@t("no charter length is refused - 250k stores without raising")
def _no_refusal():
    o = org_with()
    try:
        o.set_scope(USER, "kid", charter=body(250_000))
    except LedgerError as e:
        raise AssertionError(f"a charter length was refused - the cap is back: {e}")
    assert len(o.nodes["kid"]["charter"]) == 250_000


@t("a long charter does not block the OTHER fields in the same retool")
def _no_lockout():
    # the lockout scenario from the capped era: the gear panel resends every
    # field on every save, so a long charter riding along must never stop an
    # unrelated edit from landing.
    o = org_with(charter=body(20_000, fill="B"))
    before = snap(o)
    o.set_scope(USER, "kid", charter=o.nodes["kid"]["charter"],
                add_dirs=[{"path": "E:/w", "mode": "rw"}],
                org_visibility="full")
    assert o.nodes["kid"]["scope"]["org_visibility"] == "full"
    assert o.nodes["kid"]["scope"]["add_dirs"]
    assert len(o.nodes["kid"]["charter"]) == 20_000, "the resend cut it"
    assert snap(o) != before


@t("hire() and set_scope AGREE - neither caps, at a length that used to fail")
def _symmetry():
    # the asymmetry that caused all of this: hire never enforced, set_scope
    # enforced 4000. They must now behave identically.
    txt = body(12_345, fill="S")
    o = org_with(charter=txt)
    assert o.nodes["kid"]["charter"] == txt, "hire() altered the charter"
    o2 = org_with()
    o2.set_scope(USER, "kid", charter=txt)
    assert o2.nodes["kid"]["charter"] == txt, "set_scope altered the charter"
    assert o.nodes["kid"]["charter"] == o2.nodes["kid"]["charter"]


print(f"\nALL {PASS} CHECKS PASS")


# ============================================================================
# --discriminate: put each defect BACK and prove this file goes red.
# The mutations are applied to a TEMP COPY of the package - never the repo -
# and this file is re-run against that copy in a subprocess.
# ============================================================================
MUTATIONS = {
    "the original silent cut (truncate, say nothing)": (
        '    v = value.strip()\n',
        '    v = value.strip()[:CHARTER_LONG]\n'),
    "a hard cap that REFUSES (the ruling says uncapped)": (
        '    if len(v) > CHARTER_LONG:\n',
        '    if len(v) > CHARTER_LONG and _refuse():\n'),
    "the advisory never fires (a notice that carries no information)": (
        '    if len(v) > CHARTER_LONG:\n',
        '    if False:\n'),
}
# helper the second mutation calls, injected alongside it
_REFUSE_SRC = (
    'def _refuse():\n'
    '    raise LedgerError("charter is too long")\n'
    '\n'
    '\n')


def _discriminate():
    import shutil
    import subprocess
    here = os.path.dirname(os.path.abspath(__file__))
    src_pkg = os.path.join(here, "..", "orgtree")
    me = os.path.abspath(__file__)
    bad = 0
    for label, (find, repl) in MUTATIONS.items():
        tmp = tempfile.mkdtemp(prefix="orgtree-discrim-")
        shutil.copytree(src_pkg, os.path.join(tmp, "backend", "orgtree"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        os.makedirs(os.path.join(tmp, "backend", "tests"), exist_ok=True)
        shutil.copy(me, os.path.join(tmp, "backend", "tests",
                                     os.path.basename(me)))
        lp = os.path.join(tmp, "backend", "orgtree", "ledger.py")
        with open(lp, "r", encoding="utf-8", newline="") as fh:
            src = fh.read()
        # ⚠ this checkout is CRLF (.gitattributes wants it that way) and the
        # read above deliberately preserves that. LF-only patterns match
        # NOTHING here - the first version of this harness reported "0
        # matches" for every mutation and looked like a broken suite. Bend the
        # patterns to the file's real endings rather than rewriting the file.
        eol = "\r\n" if "\r\n" in src else "\n"
        f2, r2 = find.replace("\n", eol), repl.replace("\n", eol)
        n = src.count(f2)
        if n != 1:
            print(f"  !! MUTATION DID NOT APPLY ({n} matches): {label}")
            bad += 1
            continue
        out = src.replace(f2, r2)
        if "_refuse()" in repl:
            anchor = "def note_charter_length(".replace("\n", eol)
            out = out.replace(anchor, _REFUSE_SRC.replace("\n", eol) + anchor, 1)
        with open(lp, "w", encoding="utf-8", newline="") as fh:
            fh.write(out)
        r = subprocess.run(
            [sys.executable, os.path.join(tmp, "backend", "tests",
                                          os.path.basename(me))],
            capture_output=True, text=True)
        if r.returncode == 0:
            print(f"  !! STILL GREEN under mutation - this suite does not "
                  f"catch: {label}")
            bad += 1
        else:
            first = [ln for ln in (r.stdout + r.stderr).splitlines()
                     if "Error" in ln or "assert" in ln.lower()]
            print(f"  ok  RED under: {label}")
            print(f"        {(first or ['(no message)'])[-1][:150]}")
        shutil.rmtree(tmp, ignore_errors=True)
    if bad:
        raise SystemExit(f"\n{bad} mutation(s) did not turn this suite red")
    print("\nEVERY MUTATION TURNED THIS SUITE RED")


if "--discriminate" in sys.argv:
    print("\n§D putting the defects back, in a temp copy of the package")
    _discriminate()
