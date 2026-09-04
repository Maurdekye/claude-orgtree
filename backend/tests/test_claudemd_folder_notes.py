"""Granted-folder CLAUDE.md: the cut must DECLARE itself.

    python backend/tests/test_claudemd_folder_notes.py
    python backend/tests/test_claudemd_folder_notes.py --discriminate

WHY THIS SUITE EXISTS

`_claudemd_block` renders the CLAUDE.md of every folder an agent holds into its
prompt. It did that with a bare `.read()[:6000]` and said NOTHING - it was the
one block of the three siblings in that file that cut in silence.
`_org_charter_block` and `_standing_notes_block` both announce theirs.

MEASURED 2026-09-04, which is what turned this from tidiness into a defect: a
real granted folder on this machine holds a 58,574-char CLAUDE.md. Agents
holding that grant were handed the first 6,000 chars - 10% of the file - with
no indication that 52,574 more existed. An agent cannot tell a project whose
notes end at 6,000 chars from a project whose notes were cut there.

⚠ WHAT MAKES AN INLINE NOTICE SUFFICIENT HERE, where it was NOT sufficient for
org.md: the recipient is the one who can act. org.md's cut was announced to the
agent, but only the OPERATOR can shorten org.md, so the agent was told
something it could do nothing about. Here the agent HOLDS THE FOLDER - the
notice names the path, and the agent can simply open the file and read the
rest. Telling the reader is telling someone who can act.

DELIVERY, NOT STORAGE. Nothing in this path writes, so unlike `orgmd_get` no
byte is ever at risk on disk. That is why this is a notice and not a refusal.

    §1  an over-long folder CLAUDE.md declares the cut, with real numbers
    §2  a file that fits is rendered clean, with no notice at all
    §3  the body itself is never silently dropped, and each folder speaks for
        itself when several are granted
"""

from __future__ import annotations

import os
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

RIG = tempfile.mkdtemp(prefix="orgtree-claudemd-")
os.environ["ORGTREE_DATA"] = os.path.join(RIG, "data")
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w",
          encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

from orgtree import supervisor as S                              # noqa: E402
from orgtree.ledger import USER, Org                             # noqa: E402

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


MAX = S.CLAUDEMD_MAX
TAIL = "\n\nTHE-RULE-PAST-THE-CUT"
ALL_TOOLS = dict(bash=True, web=False, edit=True, subagents=False, mcp=[])


def folder(name, chars):
    """A granted folder holding a CLAUDE.md of exactly `chars` chars."""
    d = os.path.join(RIG, name)
    os.makedirs(d, exist_ok=True)
    body = "F" * (chars - len(TAIL)) + TAIL
    assert len(body) == chars
    with open(os.path.join(d, "CLAUDE.md"), "w", encoding="utf-8") as f:
        f.write(body)
    return d


def block(dirs):
    """`_claudemd_block` for an agent granted `dirs`."""
    org = Org.create("cmd" + str(abs(hash(tuple(dirs))) % 9999))
    org.hire(USER, None, "haiku", 10, "a",
             add_dirs=[{"path": p, "mode": "ro"} for p in dirs],
             tools=ALL_TOOLS, org_visibility="team", charter="c")
    return S._claudemd_block(org, "a")


# ============================================================================ §1
print("\n§1 an over-long folder CLAUDE.md declares its cut")

BIG = MAX + 4321
BIGDIR = folder("big", BIG)
BIGBLOCK = block([BIGDIR])


@t("the notice fires, and states the file's TRUE length")
def _declares():
    assert "TRUNCATED" in BIGBLOCK.upper(), \
        f"a {BIG}-char folder note was cut in SILENCE: {BIGBLOCK[:300]!r}"
    assert str(BIG) in BIGBLOCK, \
        "the notice does not say how long the file actually is"


@t("...and how much is missing, and how much was delivered")
def _numbers():
    assert str(MAX) in BIGBLOCK, "the notice does not state what WAS delivered"
    assert str(BIG - MAX) in BIGBLOCK, \
        f"the notice does not say the {BIG - MAX} missing chars are missing"


@t("...and names the folder, so the agent can go read the rest itself")
def _actionable():
    # this is the property that makes an inline notice enough HERE and not for
    # org.md: the recipient holds the folder and can act on what it is told.
    assert BIGDIR in BIGBLOCK, \
        f"the block does not name the folder path: {BIGBLOCK[:300]!r}"
    low = BIGBLOCK.lower()
    assert "open the file" in low or "read" in low, \
        "the notice does not tell the agent it can read the rest"


@t("the delivered body is exactly the bound, and is the HEAD of the file")
def _body():
    body = BIGBLOCK.split("---\n", 1)[1]
    assert body.startswith("F"), f"delivered the wrong end: {body[:40]!r}"
    assert TAIL.strip() not in body, \
        "the tail is present - this fixture is not actually being cut, so §1 " \
        "is proving nothing"
    assert len(body.strip()) <= MAX, len(body.strip())


# ============================================================================ §2
print("\n§2 a file that fits says nothing")


@t("POSITIVE CONTROL: a short folder CLAUDE.md carries NO truncation notice")
def _quiet():
    # without this the notice could fire unconditionally and carry no
    # information at all.
    b = block([folder("small", 400)])
    assert "TRUNCATED" not in b.upper(), \
        f"a 400-char file claimed truncation: {b[:200]!r}"
    assert TAIL.strip() in b, "a file that fits lost its tail anyway"


@t("...and exactly AT the bound is still quiet (the boundary discriminates)")
def _boundary():
    assert "TRUNCATED" not in block([folder("exact", MAX)]).upper()
    assert "TRUNCATED" in block([folder("over", MAX + 1)]).upper()


# ============================================================================ §3
print("\n§3 several folders, each speaking for itself")


@t("a cut folder and an intact folder are reported independently")
def _mixed():
    small = folder("m_small", 300)
    big = folder("m_big", MAX + 999)
    b = block([small, big])
    assert small in b and big in b, "a granted folder went missing entirely"
    # the notice must attach to the BIG one only - a blanket notice on the
    # whole block would libel the small file as incomplete
    head_small = b.split(small, 1)[1].split("\n", 1)[0]
    head_big = b.split(big, 1)[1].split("\n", 1)[0]
    assert "TRUNCATED" not in head_small.upper(), \
        f"the intact folder was labelled truncated: {head_small!r}"
    assert "TRUNCATED" in head_big.upper(), \
        f"the cut folder was not labelled: {head_big!r}"


@t("no folder's body is silently dropped when another is cut")
def _no_drop():
    small = folder("d_small", 250)
    big = folder("d_big", MAX + 50)
    b = block([small, big])
    assert b.count("--- CLAUDE.md (") == 2, \
        f"expected two folder sections, got {b.count('--- CLAUDE.md (')}"
    assert TAIL.strip() in b, "the small file's tail vanished"


print(f"\nALL {PASS} CHECKS PASS")


# ============================================================================
# --discriminate: put the silence back, in a TEMP COPY, and require red.
# ============================================================================
MUTATIONS = {
    "the original silent cut (render, say nothing)": (
        '            if len(raw) > CLAUDEMD_MAX:\n', '            if False:\n'),
    "a blanket notice on every folder (a notice that cannot discriminate)": (
        '            if len(raw) > CLAUDEMD_MAX:\n', '            if True:\n'),
}


def _discriminate():
    import shutil
    import subprocess
    src_pkg = os.path.join(HERE, "..", "orgtree")
    me = os.path.abspath(__file__)
    bad = 0
    for label, (find, repl) in MUTATIONS.items():
        tmp = tempfile.mkdtemp(prefix="orgtree-discrim-")
        shutil.copytree(src_pkg, os.path.join(tmp, "backend", "orgtree"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        os.makedirs(os.path.join(tmp, "backend", "tests"), exist_ok=True)
        shutil.copy(me, os.path.join(tmp, "backend", "tests",
                                     os.path.basename(me)))
        target = os.path.join(tmp, "backend", "orgtree", "supervisor.py")
        with open(target, "r", encoding="utf-8", newline="") as fh:
            src = fh.read()
        # ⚠ CRLF checkout: LF-only patterns match nothing. Bend the pattern to
        # the file's real endings rather than rewriting the file.
        eol = "\r\n" if "\r\n" in src else "\n"
        f2, r2 = find.replace("\n", eol), repl.replace("\n", eol)
        n = src.count(f2)
        if n != 1:
            print(f"  !! MUTATION DID NOT APPLY ({n} matches): {label}")
            bad += 1
            continue
        with open(target, "w", encoding="utf-8", newline="") as fh:
            fh.write(src.replace(f2, r2))
        r = subprocess.run(
            [sys.executable, os.path.join(tmp, "backend", "tests",
                                          os.path.basename(me))],
            capture_output=True, text=True)
        if r.returncode == 0:
            print(f"  !! STILL GREEN under mutation - not caught: {label}")
            bad += 1
        else:
            hits = [ln for ln in (r.stdout + r.stderr).splitlines()
                    if "Error" in ln or "assert" in ln.lower()]
            print(f"  ok  RED under: {label}")
            print(f"        {(hits or ['(no message)'])[-1][:150]}")
        shutil.rmtree(tmp, ignore_errors=True)
    if bad:
        raise SystemExit(f"\n{bad} mutation(s) did not turn this suite red")
    print("\nEVERY MUTATION TURNED THIS SUITE RED")


if "--discriminate" in sys.argv:
    print("\n§D putting the silence back, in a temp copy of the package")
    _discriminate()
