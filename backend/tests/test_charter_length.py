"""Charter length - text must never be cut in silence.

    python backend/tests/test_charter_length.py     (no pytest; plain asserts)

WHY THIS SUITE EXISTS

`set_scope` used to store charters as `charter.strip()[:4000]`. Over-long text
was cut mid-word and NOTHING said so - not the return value, not a warning, not
the UI. This org's own shipped team charter had been ending mid-sentence for an
unknown period and no agent ever read its final rule. User ruling 2026-09-04:
"warn or refuse instead of cutting".

The contract this file pins down:

  * a too-long charter EDIT is REFUSED, with the measured length and the limit
    in the message, and NOTHING is written (the ledger's atomicity contract);
  * an UNCHANGED over-long charter is accepted WHOLE, with a loud warning.

⚠ THAT SECOND RULE IS THE ONE THAT MATTERS MOST, AND IT IS NOT A COURTESY.
`hire()` does not limit charter length, so over-long charters already exist -
the shipped preset docs/charters/coordinator.md is itself over the limit. The
gear panel sends EVERY field on every save (`modals.tsx` doSave), so a folders
or effort edit on such a node resends its own over-long charter untouched.
Refusing that would convert a silent bug into a HARD LOCKOUT: the node could
never be retooled again, for anything at all. §3 is that scenario end to end.

Every section carries a POSITIVE CONTROL, because most of these assertions are
about something NOT happening (no cut, no refusal, no lockout) and an assertion
like that is worthless unless the opposite case is shown to fire.

    §1  the refusal: boundary, message content, and atomicity
    §2  no truncation, ever - the tail survives on every accepted path
    §3  the lockout case: a pre-existing over-long charter stays editable
    §4  the carve-out cannot be abused to write NEW over-long text
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

from orgtree.ledger import CHARTER_MAX, USER, LedgerError, Org   # noqa: E402

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


def org_with(charter=None, team_charter=None):
    """A two-node org; `kid` optionally seeded with over-long text via hire(),
    which is the only path that can create one (hire does not enforce)."""
    o = Org.create("clen")
    o.hire(USER, None, "haiku", 10, "boss", **SPEC)
    spec = dict(SPEC)
    if charter is not None:
        spec["charter"] = charter
    o.hire("boss", "boss", "haiku", 0, "kid", **spec)
    if team_charter is not None:
        o.nodes["kid"]["team_charter"] = team_charter   # seed directly
    return o


def snap(o):
    return json.dumps(o.d, sort_keys=True, default=str)


def refusal(fn):
    """Run `fn`, require a LedgerError, return its message."""
    try:
        fn()
    except LedgerError as e:
        return str(e)
    raise AssertionError("expected a LedgerError, got success")


# ============================================================================ §1
print("\n§1 the refusal: boundary, message, atomicity")


@t(f"a charter of exactly CHARTER_MAX ({CHARTER_MAX}) is ACCEPTED, whole")
def _at_limit():
    o = org_with()
    txt = body(CHARTER_MAX)
    assert len(txt) == CHARTER_MAX
    o.set_scope(USER, "kid", charter=txt)
    got = o.nodes["kid"]["charter"]
    assert len(got) == CHARTER_MAX, f"stored {len(got)}, wrote {CHARTER_MAX}"
    assert got.endswith(TAIL), f"the tail was cut: {got[-40:]!r}"


@t("one character over the limit is REFUSED - the boundary discriminates")
def _one_over():
    # ⚠ this is the check that makes the one above non-vacuous: without it,
    # a guard that accepted everything would pass §1's first check happily.
    o = org_with()
    msg = refusal(lambda: o.set_scope(USER, "kid", charter=body(CHARTER_MAX + 1)))
    assert str(CHARTER_MAX + 1) in msg, \
        f"the refusal did not state the measured length: {msg}"


@t("the refusal states the MEASURED length, the limit, and the overage")
def _message():
    o = org_with()
    n = CHARTER_MAX + 516
    msg = refusal(lambda: o.set_scope(USER, "kid", team_charter=body(n)))
    for want in (str(n), str(CHARTER_MAX), "516", "team_charter"):
        assert want in msg, f"{want!r} missing from the refusal: {msg}"
    assert "byte" in msg.lower(), f"no byte count in the refusal: {msg}"


@t("a REFUSED charter edit leaves the org byte-identical (atomicity)")
def _atomic():
    # the charter is validated BEFORE the first mutation, so a retool that
    # also moves dirs/tools must write NEITHER when the charter is too long
    o = org_with()
    before = snap(o)
    refusal(lambda: o.set_scope(
        USER, "kid", charter=body(CHARTER_MAX + 200),
        add_dirs=[{"path": "E:/w", "mode": "rw"}],
        org_visibility="full"))
    assert snap(o) == before, \
        "a refused retool mutated the org - the charter check runs too late"


@t("POSITIVE CONTROL: the same retool WITHOUT the long charter does mutate")
def _atomic_control():
    # without this, _atomic would pass against a set_scope that never wrote
    # anything at all, and would be proving nothing.
    o = org_with()
    before = snap(o)
    o.set_scope(USER, "kid", charter=body(100),
                add_dirs=[{"path": "E:/w", "mode": "rw"}],
                org_visibility="full")
    assert snap(o) != before, "the control retool changed nothing - §1 is inert"
    assert o.nodes["kid"]["scope"]["org_visibility"] == "full"


@t("a multibyte charter is limited by CHARACTERS, and reports BYTES")
def _multibyte():
    o = org_with()
    # 'é' is 1 char / 2 bytes: at the char limit this is legal but ~2x in bytes
    ok_txt = "é" * CHARTER_MAX
    o.set_scope(USER, "kid", charter=ok_txt)
    assert len(o.nodes["kid"]["charter"]) == CHARTER_MAX
    assert len(o.nodes["kid"]["charter"].encode("utf-8")) == CHARTER_MAX * 2
    msg = refusal(lambda: o.set_scope(USER, "kid", charter="é" * (CHARTER_MAX + 1)))
    assert str((CHARTER_MAX + 1) * 2) in msg, \
        f"the refusal did not report the BYTE count: {msg}"


# ============================================================================ §2
print("\n§2 no truncation, ever")


@t("an accepted charter is stored EXACTLY as written - no slicing")
def _exact():
    o = org_with()
    for n in (1, 500, CHARTER_MAX - 1, CHARTER_MAX):
        txt = body(max(n, len(TAIL)))
        o.set_scope(USER, "kid", charter=txt)
        got = o.nodes["kid"]["charter"]
        assert got == txt.strip(), \
            f"len {n}: stored {len(got)} chars, wrote {len(txt)}"


@t("no accepted path anywhere produces a value of exactly CHARTER_MAX by CUTTING")
def _no_cut():
    # the old bug's fingerprint: a stored charter that is exactly the cap AND
    # has lost its tail. Writing over-long text must now never yield that.
    o = org_with()
    txt = body(CHARTER_MAX + 900)
    refusal(lambda: o.set_scope(USER, "kid", charter=txt))
    got = o.nodes["kid"]["charter"]
    assert got == "seed charter", \
        f"a refused write still altered the charter: {got[:60]!r}"
    assert not (len(got) == CHARTER_MAX and not got.endswith(TAIL)), \
        "this is the exact shape of the old silent truncation"


@t("clearing a charter with '' still works (empty ⇒ None)")
def _clear():
    o = org_with()
    o.set_scope(USER, "kid", charter="")
    assert o.nodes["kid"]["charter"] is None, o.nodes["kid"]["charter"]


# ============================================================================ §3
print("\n§3 the lockout case - the thing that would make this fix worthless")

OVER = body(CHARTER_MAX + 1218, fill="B")


@t(f"setup: hire() stores an over-long charter untouched ({len(OVER)} chars)")
def _hire_untouched():
    # if this ever starts truncating or refusing, §3 stops describing reality
    # and the shipped coordinator.md preset (over the limit) stops being
    # hireable - so this is a real assertion, not scaffolding.
    o = org_with(charter=OVER)
    got = o.nodes["kid"]["charter"]
    assert len(got) == len(OVER), f"hire() changed the length: {len(got)}"
    assert got.endswith(TAIL), "hire() cut the tail"


@t("an UNRELATED retool on that node SUCCEEDS - it is not locked out")
def _not_locked_out():
    o = org_with(charter=OVER)
    r = o.set_scope(USER, "kid", charter=OVER,          # resent, unchanged
                    add_dirs=[{"path": "E:/w", "mode": "rw"}])
    assert o.nodes["kid"]["scope"]["add_dirs"], "the unrelated edit did not land"
    assert isinstance(r, dict)


@t("...and the over-long charter is kept WHOLE, not cut back to the limit")
def _kept_whole():
    o = org_with(charter=OVER)
    o.set_scope(USER, "kid", charter=OVER,
                add_dirs=[{"path": "E:/w", "mode": "rw"}])
    got = o.nodes["kid"]["charter"]
    assert len(got) == len(OVER), \
        f"the resend cut it to {len(got)} - that is the original bug"
    assert got.endswith(TAIL), f"the tail was lost: {got[-40:]!r}"


@t("...and it is LOUD: a warning names the length, the limit and the overage")
def _loud():
    o = org_with(charter=OVER)
    r = o.set_scope(USER, "kid", charter=OVER,
                    add_dirs=[{"path": "E:/w", "mode": "rw"}])
    ws = [w for w in (r.get("warnings") or []) if "charter" in w]
    assert ws, f"the over-long resend was SILENT: {r.get('warnings')!r}"
    joined = " ".join(ws)
    for want in (str(len(OVER)), str(CHARTER_MAX), "1218"):
        assert want in joined, f"{want!r} missing from the warning: {joined}"


@t("POSITIVE CONTROL: a NORMAL save emits no charter warning at all")
def _quiet_control():
    # without this, _loud could pass against code that warns on every save,
    # and the warning would carry no information.
    o = org_with()
    r = o.set_scope(USER, "kid", charter=body(200),
                    add_dirs=[{"path": "E:/w", "mode": "rw"}])
    ws = [w for w in (r.get("warnings") or []) if "char" in w and "limit" in w]
    assert not ws, f"a normal save warned about length: {ws!r}"


@t("the same carve-out works for team_charter")
def _team_carveout():
    o = org_with()
    o.nodes["kid"]["team_charter"] = OVER
    r = o.set_scope(USER, "kid", team_charter=OVER, org_visibility="full")
    assert o.nodes["kid"]["team_charter"] == OVER, "team_charter was cut"
    assert o.nodes["kid"]["scope"]["org_visibility"] == "full"
    assert any("team_charter" in w for w in (r.get("warnings") or [])), \
        r.get("warnings")


# ============================================================================ §4
print("\n§4 the carve-out cannot be abused")


@t("a DIFFERENT over-long value is still refused, even on an over-long node")
def _no_smuggling():
    # the carve-out matches only what is ALREADY stored. It must not become a
    # general "this node may have long charters now" exemption.
    o = org_with(charter=OVER)
    other = body(CHARTER_MAX + 1218, fill="C")
    assert len(other) == len(OVER) and other != OVER
    msg = refusal(lambda: o.set_scope(USER, "kid", charter=other))
    assert str(len(other)) in msg, msg
    assert o.nodes["kid"]["charter"] == OVER, "the refused value was written"


@t("whitespace-only differences do not count as 'changed' (strip is applied)")
def _strip_equiv():
    o = org_with(charter=OVER)
    o.set_scope(USER, "kid", charter="\n  " + OVER + "  \n")
    assert o.nodes["kid"]["charter"] == OVER.strip()


@t("SHORTENING an over-long charter is always allowed - the way back out")
def _can_shorten():
    # if this ever fails, an over-long charter really is unfixable and the
    # carve-out has become a trap rather than an escape hatch.
    o = org_with(charter=OVER)
    o.set_scope(USER, "kid", charter=body(300))
    assert len(o.nodes["kid"]["charter"]) == 300


print(f"\nALL {PASS} CHECKS PASS")


# ============================================================================
# --discriminate: put each defect BACK and prove this file goes red.
# A suite that has never been seen failing is not evidence of anything. The
# mutations are applied to a TEMP COPY of the package - never the repo - and
# this file is re-run against that copy in a subprocess.
# ============================================================================
MUTATIONS = {
    "the original silent cut (truncate, say nothing)": (
        '    v = value.strip()\n'
        '    if len(v) <= CHARTER_MAX:\n'
        '        return v\n',
        '    v = value.strip()\n'
        '    if True:\n'
        '        return v[:CHARTER_MAX]\n'),
    "refuse ALWAYS (no unchanged-resend carve-out ⇒ the lockout)": (
        '    if v == (current or "").strip():\n',
        '    if False:\n'),
}


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
        # read/write in BINARY with newline="" preserved: this copy must keep
        # whatever line endings the checkout has, and nothing here may ever
        # rewrite a file in the repo itself.
        with open(lp, "r", encoding="utf-8", newline="") as fh:
            src = fh.read()
        # ⚠ this checkout is CRLF (.gitattributes wants it that way) and the
        # read above deliberately preserves that. LF-only patterns match
        # NOTHING here - the first version of this harness reported "0
        # matches" for both mutations and looked like a broken suite. Bend the
        # patterns to the file's real endings rather than rewriting the file.
        eol = "\r\n" if "\r\n" in src else "\n"
        find, repl = find.replace("\n", eol), repl.replace("\n", eol)
        n = src.count(find)
        if n != 1:
            print(f"  !! MUTATION DID NOT APPLY ({n} matches): {label}")
            bad += 1
            continue
        with open(lp, "w", encoding="utf-8", newline="") as fh:
            fh.write(src.replace(find, repl))
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
