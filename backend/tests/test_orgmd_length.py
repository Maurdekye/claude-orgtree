"""org.md: the operator learns that their charter is delivered SHORT.

    python backend/tests/test_orgmd_length.py     (no pytest; plain asserts)
    python backend/tests/test_orgmd_length.py --discriminate

WHY THIS SUITE EXISTS

`_org_charter_block` cuts org.md at ORG_CHARTER_MAX before putting it in every
agent's system prompt, and it DOES announce the cut - inline, in the prompt,
to the agent. That is worth keeping: an agent whose standing instructions are
incomplete should know. But it is only half a fix, because THE AGENT CANNOT ACT
ON IT. org.md lives in the operator's workspace; the operator is the only party
who can shorten it, and the operator was the one party never told.

So this is the same defect as the charter and preset cuts, wearing a disguise:
announcing a cut to somebody who cannot fix it is not telling anyone.

⚠ TWO DIFFERENT LENGTHS, AND CONFLATING THEM IS THE BUG. The FILE is always
saved whole - nothing truncates storage. What is bounded is DELIVERY. A
correct message therefore says "saved whole, delivered short", never "too
long"; §2 asserts that wording, because "too long" would send the operator
hunting for a save error that never happened.

⚠ AND A SHARPER HAZARD ON THE READ SIDE. The editor's GET used to hand back
`...read()[:60000]` with no flag. A file over that read short, and one
ordinary save then wrote the short copy back over the whole file - real,
permanent loss of the operator's text, unlike the delivery cut which loses
nothing on disk. App.tsx already carried the scar of the sibling bug (a failed
read arming an empty write). §3 pins the flag that lets the client disarm the
save.

    §1  the read declares the file's true length, and when it was cut
    §2  the save says "stored whole, delivered short" - and stays quiet when
        the file fits
    §3  storage is never truncated, at any length
    §4  the INLINE announcement to the agent still exists (not traded away)
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

# an isolated data root BEFORE any orgtree import - store binds ORGTREE_DATA at
# import time, and importing api imports store. mkdtemp also keeps
# net._default_address off the operator's real mail hub.
RIG = tempfile.mkdtemp(prefix="orgtree-orgmdlen-")
os.environ["ORGTREE_DATA"] = os.path.join(RIG, "data")
WS = os.path.join(RIG, "workspace")
for _d in (os.environ["ORGTREE_DATA"], WS):
    os.makedirs(_d, exist_ok=True)
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w",
          encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

from orgtree import api                                          # noqa: E402
from orgtree import supervisor as S                              # noqa: E402
from orgtree import store                                        # noqa: E402
from orgtree.ledger import Org                                   # noqa: E402

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


class FakeReq:
    """Enough of a Request for `_public_slug`, which reads request.state."""
    class _S:
        public_slug = None
    state = _S()


SLUG = "orgmdlen"
ORG = Org.create(SLUG)
ORG.d["workspace"] = WS
store.save_org(ORG)

PROMPT_MAX = S.ORG_CHARTER_MAX
MD = os.path.join(WS, "CLAUDE.md")
TAIL = "\n\nTHE-LAST-RULE-NOBODY-EVER-READ"


def write_md(n):
    """Write a CLAUDE.md of exactly n chars, ending in a recognisable tail."""
    body = "R" * (n - len(TAIL)) + TAIL
    assert len(body) == n
    with open(MD, "w", encoding="utf-8") as f:
        f.write(body)
    return body


def get():
    return api.orgmd_get(SLUG, FakeReq())


def put(content):
    return asyncio.run(api.orgmd_put(SLUG, api.OrgMd(content=content)))


# ============================================================================ §1
print("\n§1 the read declares the true length")


@t("a short file reports its exact length and read_truncated=False")
def _short_read():
    body = write_md(500)
    r = get()
    assert r["chars"] == 500, r["chars"]
    assert r["read_truncated"] is False, r["read_truncated"]
    assert r["content"] == body, "the editor was handed something else"


@t("the read payload states BOTH bounds, so no client hardcodes them")
def _bounds():
    r = get()
    assert r["edit_max"] == api.ORGMD_EDIT_MAX, r["edit_max"]
    assert r["prompt_max"] == PROMPT_MAX, r["prompt_max"]
    # they measure different things: what the EDITOR loads vs what an AGENT
    # receives. A client that confuses them tells the operator the wrong thing.
    assert r["edit_max"] > r["prompt_max"], (r["edit_max"], r["prompt_max"])


@t("a file over the EDIT bound reports its true length and read_truncated=True")
def _long_read():
    write_md(api.ORGMD_EDIT_MAX + 250)
    r = get()
    assert r["chars"] == api.ORGMD_EDIT_MAX + 250, r["chars"]
    assert r["read_truncated"] is True, \
        "a cut read did not declare itself - the client cannot disarm the save"
    assert len(r["content"]) == api.ORGMD_EDIT_MAX, len(r["content"])
    assert r["chars"] > len(r["content"]), "chars must be the PRE-cut length"


@t("POSITIVE CONTROL: read_truncated discriminates (it is not hardcoded True)")
def _read_flag_control():
    write_md(300)
    assert get()["read_truncated"] is False, \
        "read_truncated is stuck True - §1's long-read check proves nothing"


# ============================================================================ §2
print("\n§2 the save tells the operator what agents will actually receive")


@t("a file over the PROMPT bound produces a warning naming both numbers")
def _put_warns():
    n = PROMPT_MAX + 777
    r = put("Z" * n)
    ws = r.get("warnings") or []
    assert ws, f"a {n}-char org.md saved SILENTLY: {r!r}"
    joined = " ".join(ws)
    for want in (str(n), str(PROMPT_MAX), "777"):
        assert want in joined, f"{want!r} missing from the warning: {joined}"
    assert r["prompt_truncated"] is True, r


@t("the warning says STORED WHOLE and DELIVERED short - not 'too long'")
def _put_wording():
    joined = " ".join(put("Z" * (PROMPT_MAX + 50)).get("warnings") or []).lower()
    assert "whole" in joined, joined
    # the file saved fine. Wording that implies otherwise sends the operator
    # looking for a failure that did not happen.
    for banned in ("too long", "not saved", "failed", "refus"):
        assert banned not in joined, \
            f"the warning implies the SAVE failed ({banned!r}): {joined}"


@t("the warning says the operator is the one who must act")
def _put_actionable():
    joined = " ".join(put("Z" * (PROMPT_MAX + 50)).get("warnings") or []).lower()
    assert "you" in joined or "trim" in joined or "shorten" in joined, joined


@t("POSITIVE CONTROL: a file that FITS produces no warning at all")
def _put_quiet():
    # without this the warning could fire unconditionally and mean nothing.
    r = put("Z" * 200)
    assert not (r.get("warnings") or []), f"a short org.md warned: {r!r}"
    assert r["prompt_truncated"] is False, r


@t("...and exactly AT the bound is still quiet (the boundary discriminates)")
def _put_boundary():
    assert not (put("Z" * PROMPT_MAX).get("warnings") or [])
    assert put("Z" * (PROMPT_MAX + 1)).get("warnings")


# ============================================================================ §3
print("\n§3 storage is never truncated - only delivery is")


@t("the FILE keeps every character, well past both bounds")
def _storage_whole():
    n = api.ORGMD_EDIT_MAX + 5_000
    body = "Q" * (n - len(TAIL)) + TAIL
    put(body)
    on_disk = open(MD, encoding="utf-8").read()
    assert len(on_disk) == n, f"the save truncated storage: {len(on_disk)} vs {n}"
    assert on_disk.endswith(TAIL), "the tail was lost on disk"


@t("a save never silently shortens a file that was already long")
def _no_roundtrip_loss():
    # the round-trip hazard: read short -> save -> file rewritten short. The
    # read must FLAG itself so a client can refuse; this asserts the flag is
    # the only thing standing between the operator and real data loss.
    write_md(api.ORGMD_EDIT_MAX + 400)
    r = get()
    assert r["read_truncated"] is True
    assert len(r["content"]) < r["chars"], "the partial read is not partial?"
    # a client that ignored the flag and saved r["content"] back would lose
    # r["chars"] - len(r["content"]) chars. Prove that loss is real, so the
    # flag is doing load-bearing work rather than decorating the payload.
    put(r["content"])
    assert len(open(MD, encoding="utf-8").read()) == api.ORGMD_EDIT_MAX, (
        "saving a truncated read did NOT shorten the file - if this is no "
        "longer possible, say why here; until then the flag is what prevents "
        "it and the UI must keep honouring it")


# ============================================================================ §4
print("\n§4 the agent-facing announcement is still there")


@t("an over-long org.md still tells the AGENT its copy was cut")
def _inline_kept():
    # point of order: the writer-facing warning ADDS to this, it does not
    # replace it. An agent running on incomplete instructions must still know.
    write_md(PROMPT_MAX + 3_000)
    org = store.load_org(SLUG)
    block = S._org_charter_block(org)
    assert "TRUNCATED" in block.upper(), \
        f"the inline announcement was traded away: {block[:400]!r}"
    assert str(PROMPT_MAX) in block, "the inline notice lost its number"


@t("POSITIVE CONTROL: a fitting org.md carries NO truncation notice")
def _inline_quiet():
    write_md(400)
    block = S._org_charter_block(store.load_org(SLUG))
    assert "TRUNCATED" not in block.upper(), \
        "every charter claims truncation - §4's check proves nothing"
    assert "ORG CHARTER" in block, "the charter block vanished entirely"


print(f"\nALL {PASS} CHECKS PASS")


# ============================================================================
# --discriminate: put the defect back in a TEMP COPY and require red.
# ============================================================================
MUTATIONS = {
    "the save says nothing (the original writer-facing silence)": (
        "api.py", '    if over > 0:\n', '    if False:\n'),
    "the read hides that it cut (the round-trip data-loss hazard)": (
        "api.py", '        read_cut = chars > ORGMD_EDIT_MAX\n',
        '        read_cut = False\n'),
    "the inline agent-facing notice is dropped": (
        "supervisor.py", '    cut = len(txt) > ORG_CHARTER_MAX\n',
        '    cut = False\n'),
}


def _discriminate():
    import shutil
    import subprocess
    src_pkg = os.path.join(HERE, "..", "orgtree")
    me = os.path.abspath(__file__)
    bad = 0
    for label, (fname, find, repl) in MUTATIONS.items():
        tmp = tempfile.mkdtemp(prefix="orgtree-discrim-")
        shutil.copytree(src_pkg, os.path.join(tmp, "backend", "orgtree"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        os.makedirs(os.path.join(tmp, "backend", "tests"), exist_ok=True)
        shutil.copy(me, os.path.join(tmp, "backend", "tests",
                                     os.path.basename(me)))
        target = os.path.join(tmp, "backend", "orgtree", fname)
        with open(target, "r", encoding="utf-8", newline="") as fh:
            src = fh.read()
        # ⚠ CRLF checkout: LF-only patterns match nothing here. Bend the
        # pattern to the file's real endings rather than rewriting the file.
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
    print("\n§D putting the defects back, in a temp copy of the package")
    _discriminate()
