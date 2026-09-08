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


def payload(dirpath, user_dirpath=None):
    """GET /api/charters against `dirpath` (repo) and, if given, `user_dirpath`
    (user-space) — the whole payload. Repo-only callers leave
    `_user_charters_dir` unpatched: it resolves against store.DATA_ROOT, which
    is the isolated tmpdir this file set at import (module docstring), so it
    is a no-op — never a real developer's own presets.

    `_user_charters_dir` is resolved PER CALL (not a module constant — see its
    own docstring for why), so it is monkeypatched as a function here, not
    assigned as a plain attribute."""
    old_repo, old_user_fn = api.CHARTERS_DIR, api._user_charters_dir
    api.CHARTERS_DIR = dirpath
    if user_dirpath is not None:
        api._user_charters_dir = lambda: user_dirpath
    try:
        out = api.charters_list()
    finally:
        api.CHARTERS_DIR, api._user_charters_dir = old_repo, old_user_fn
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
# ⚠ SIZED FROM api.PRESET_MAX, never a literal. When the bound was raised from
# 6000 to 100_000 a hardcoded 7000-char fixture stopped being truncated at all,
# and the truncation check below would have passed while testing NOTHING - the
# textbook vacuous pass. Derived, this fixture is over the bound by
# construction whatever the bound becomes.
BIG_CHARS = api.PRESET_MAX + 500
write("big.md", ("x\n\n---\n\n" + "y" * BIG_CHARS + "\n").replace("\n", "\r\n"))
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


@t("the preset sanity bound still cuts - and is DECLARED, never silent")
def _trunc():
    # History: this read "the 6000-char truncation is unchanged" and asserted
    # only len == 6000. The bound survives because this endpoint serializes
    # whatever .md files exist into one browser response, but a cut that says
    # nothing is the defect - the hire form offered a card whose text simply
    # stopped. The record must CARRY the true length and admit it was cut.
    r = RECS["big"]
    c = r["content"]
    assert len(c) == api.PRESET_MAX, \
        f"expected the {api.PRESET_MAX} bound, got {len(c)}"
    assert c.startswith("y"), f"truncated the wrong end: {c[:40]!r}"
    assert r.get("truncated") is True, \
        f"a cut body did not report truncated=True: {r.get('truncated')!r}"
    assert r.get("chars") == BIG_CHARS, (
        f"the record must carry the body's TRUE length ({BIG_CHARS}) so the "
        f"UI can say what was lost; got {r.get('chars')!r}")
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


@t("the payload states both numbers, so no client has to hardcode them")
def _limits():
    p = payload(FIX)
    assert p.get("preset_max") == api.PRESET_MAX, p.get("preset_max")
    # ⚠ charter_long is an ADVISORY THRESHOLD, not a cap. Charters are
    # uncapped (user ruling 2026-09-04 "uncap it"); this is only the length
    # above which the hire form mentions the per-turn prompt cost. A client
    # that treats it as a limit reintroduces the bug.
    from orgtree import ledger as _lg
    assert p.get("charter_long") == _lg.CHARTER_LONG, p.get("charter_long")
    assert "charter_max" not in p, (
        "charter_max is gone - the cap was removed. A client still reading it "
        "would silently get `undefined` and stop warning at all")


@t("REGRESSION: no charter cap survives anywhere in the served contract")
def _no_cap():
    # the endpoint must not hand a client any number it could enforce as a
    # charter maximum. preset_max bounds the PRESET FILE, not the charter.
    p = payload(FIX)
    from orgtree import ledger as _lg
    assert not hasattr(_lg, "CHARTER_MAX"), \
        "ledger.CHARTER_MAX is back - charters are supposed to be uncapped"
    assert p["preset_max"] > _lg.CHARTER_LONG * 10, (
        "the preset bound should sit far above any real charter, so it never "
        f"bites in practice: {p['preset_max']} vs advisory {_lg.CHARTER_LONG}")


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


@t("served lengths, recorded - against the bound and the advisory")
def _lengths():
    from orgtree import ledger as _lg
    over = []
    for f in FILES:
        c = SERVED[f[:-3].replace("-", " ")]
        raw = os.path.getsize(os.path.join(REAL, f))
        flag = "  << long (advisory only)" if len(c) > _lg.CHARTER_LONG else ""
        if flag:
            over.append(f)
        print(f"        {f:16s} file {raw:6d} B   served {len(c):5d} chars"
              f"   bound headroom {api.PRESET_MAX - len(c):6d}{flag}")
    if over:
        # NOT a failure and NOT a limit: charters are uncapped. These presets
        # are hireable and editable at any length. The only consequence is
        # cost - the text rides in the agent's prompt every turn - which the
        # draft card mentions and this line records.
        print(f"\n        note: {over} exceed the {_lg.CHARTER_LONG}-char "
              "advisory threshold. Nothing refuses or truncates them; they "
              "simply cost tokens on every turn of that agent's life.")


# ============================================================================ §3
print("\n§3 the user-space directory (_user_charters_dir()) and its merge")

REPO3 = tempfile.mkdtemp(prefix="orgtree-charters-repo3-")
USER3 = tempfile.mkdtemp(prefix="orgtree-charters-user3-")


def w3(dirpath, name, text):
    with open(os.path.join(dirpath, name), "wb") as f:
        f.write(text.encode("utf-8"))


w3(REPO3, "alpha.md", "repo alpha body\n")
w3(REPO3, "beta.md", "repo beta body (should be overridden on collision)\n")
w3(USER3, "beta.md", "user beta body (collides with the repo's beta.md)\n")
w3(USER3, "gamma.md", "user gamma body, unique to the user directory\n")
w3(USER3, "notes.txt", "not a preset - must be ignored\n")


@t("a missing user directory is a silent no-op — repo presets still serve")
def _user_dir_missing():
    p = payload(REPO3, os.path.join(USER3, "does-not-exist"))
    names = {c["name"] for c in p["charters"]}
    assert names == {"alpha", "beta"}, names
    assert all(c["source"] == "repo" for c in p["charters"]), p["charters"]


@t("an empty user directory is a silent no-op — repo presets still serve")
def _user_dir_empty():
    empty = tempfile.mkdtemp(prefix="orgtree-charters-empty-")
    p = payload(REPO3, empty)
    names = {c["name"] for c in p["charters"]}
    assert names == {"alpha", "beta"}, names


@t("a non-.md file in the user directory is ignored")
def _user_dir_ignores_non_md():
    p = payload(REPO3, USER3)
    names = {c["name"] for c in p["charters"]}
    assert "notes" not in names, names


@t("a user-only preset is served, tagged source=user")
def _user_only_preset():
    p = payload(REPO3, USER3)
    gammas = [c for c in p["charters"] if c["name"] == "gamma"]
    assert len(gammas) == 1, gammas
    assert gammas[0]["source"] == "user", gammas[0]
    assert "unique to the user directory" in gammas[0]["content"]


@t("a filename collision serves only the user preset and logs the override")
def _collision_user_wins():
    p = payload(REPO3, USER3)
    betas = [c for c in p["charters"] if c["name"] == "beta"]
    assert len(betas) == 1, f"expected only user beta.md, got {betas}"
    assert betas[0]["source"] == "user", betas
    assert "user beta body" in betas[0]["content"]


@t("shadowing compares filenames, not display names")
def _collision_is_filename_based():
    repo = tempfile.mkdtemp(prefix="orgtree-charters-filenames-repo-")
    user = tempfile.mkdtemp(prefix="orgtree-charters-filenames-user-")
    w3(repo, "team-lead.md", "repo team lead\n")
    w3(user, "team lead.md", "user team lead\n")
    presets = [c for c in payload(repo, user)["charters"] if c["name"] == "team lead"]
    assert len(presets) == 2, presets
    assert {c["source"] for c in presets} == {"repo", "user"}, presets


@t("a filename override logs both the repo and user paths")
def _collision_logs_override():
    import contextlib
    import io
    stream = io.StringIO()
    with contextlib.redirect_stdout(stream):
        payload(REPO3, USER3)
    log = stream.getvalue()
    assert "[orgtree] charters: repo preset" in log, log
    assert os.path.join(REPO3, "beta.md") in log, log
    assert os.path.join(USER3, "beta.md") in log, log


@t("every repo preset is tagged source=repo")
def _repo_tagged():
    p = payload(REPO3, USER3)
    alphas = [c for c in p["charters"] if c["name"] == "alpha"]
    assert len(alphas) == 1 and alphas[0]["source"] == "repo", alphas


@t("REGRESSION: _user_charters_dir() is resolved PER CALL, not frozen at "
   "import — it follows store.DATA_ROOT set AFTER api was imported")
def _user_charters_dir_follows_data_root():
    # the exact idiom test_prime_restart.py and test_watchdog_visibility.py
    # already use for the same class of bug: reassign store.DATA_ROOT, call,
    # restore. A module-level constant computed at import time would still
    # point at the OLD value here and this check would fail.
    from orgtree import store as _store
    real = _store.DATA_ROOT
    moved = tempfile.mkdtemp(prefix="orgtree-charters-dataroot-")
    try:
        _store.DATA_ROOT = moved
        got = api._user_charters_dir()
    finally:
        _store.DATA_ROOT = real
    assert got == os.path.normpath(os.path.join(moved, "user", "charters")), got


@t("ORGTREE_USER_CHARTERS, when set, overrides the DATA_ROOT-derived default")
def _user_charters_dir_env_override():
    old = os.environ.get("ORGTREE_USER_CHARTERS")
    override_dir = tempfile.mkdtemp(prefix="orgtree-charters-envoverride-")
    os.environ["ORGTREE_USER_CHARTERS"] = override_dir
    try:
        got = api._user_charters_dir()
    finally:
        if old is None:
            os.environ.pop("ORGTREE_USER_CHARTERS", None)
        else:
            os.environ["ORGTREE_USER_CHARTERS"] = old
    assert got == os.path.normpath(override_dir), got


# far larger than PRESET_MAX (the SERVED bound) and larger than the earlier
# `big.md` fixture (PRESET_MAX+500) — this exercises READ_CAP (the READ
# bound), which `big.md` sits nowhere near. A stray multi-MB non-preset file
# dropped into a user's charters directory must not be read in full.
HUGE_CHARS = api.READ_CAP + 5000
write("huge.md", ("x\n\n---\n\n" + "z" * HUGE_CHARS + "\n"))


@t("a file far larger than READ_CAP is served bounded, `chars` reported "
   "unknown (None) rather than a wrong number")
def _read_cap_bounds_huge_file():
    p = payload(REPO3, FIX)  # FIX (§1) now also holds huge.md
    huge = [c for c in p["charters"] if c["name"] == "huge"]
    assert len(huge) == 1, huge
    r = huge[0]
    assert len(r["content"]) == api.PRESET_MAX, (
        f"expected the served bound {api.PRESET_MAX}, got {len(r['content'])}")
    assert r["truncated"] is True, r
    assert r.get("chars") is None, (
        "a file this large was fully read to compute an exact `chars` — "
        f"defeats the point of READ_CAP: {r.get('chars')!r}")


@t("a file just over PRESET_MAX but well under READ_CAP still reports an "
   "exact `chars` (the existing big.md fixture, unaffected by the new cap)")
def _read_cap_does_not_bite_moderate_files():
    # positive control for the test above: proves READ_CAP has real headroom
    # over PRESET_MAX rather than being the same bound under a new name
    p = payload(REPO3, FIX)
    big = [c for c in p["charters"] if c["name"] == "big"]
    assert len(big) == 1, big
    assert big[0].get("chars") == BIG_CHARS, big[0]


@t("POSITIVE CONTROL: a header just under HEADER_SCAN_CAP still splits "
   "normally, body served in full")
def _header_under_scan_cap_ok():
    header = "H" * (api.HEADER_SCAN_CAP - 100)
    write("headerok.md", header + "\n---\n\n" + BODY + "\n")
    p = payload(REPO3, FIX)
    recs = {c["name"]: c for c in p["charters"]}
    assert "headerok" in recs, sorted(recs)
    assert BODY in recs["headerok"]["content"], recs["headerok"]["content"][:200]
    assert "H" * 10 not in recs["headerok"]["content"], (
        "the header leaked into the served body")


@t("REGRESSION: a header that exceeds HEADER_SCAN_CAP (but not READ_CAP) is "
   "refused, never served as the charter body")
def _header_over_scan_cap_refused():
    # the bounded read (READ_CAP) defeats the header/body split if the text
    # BEFORE the separator is itself larger than what we bothered to scan for
    # a separator in. This header comfortably fits inside READ_CAP, so it
    # isolates HEADER_SCAN_CAP specifically (the next test covers READ_CAP).
    header = "H" * (api.HEADER_SCAN_CAP + 1000)
    write("oversizedheader.md", header + "\n---\n\n" + BODY + "\n")
    p = payload(REPO3, FIX)
    names = {c["name"] for c in p["charters"]}
    assert "oversizedheader" not in names, (
        f"a header past HEADER_SCAN_CAP was served as a preset body: {sorted(names)}")


@t("REGRESSION: a header that exceeds READ_CAP entirely is refused, never "
   "served as the charter body (redteam's exact repro — a 1.26M-char header "
   "that was previously served verbatim as the charter)")
def _header_over_read_cap_refused():
    header = "H" * (api.READ_CAP + 1000)
    write("hugeheader.md", header + "\n---\n\n" + BODY + "\n")
    p = payload(REPO3, FIX)
    names = {c["name"] for c in p["charters"]}
    assert "hugeheader" not in names, (
        f"a header past READ_CAP was served as a preset body: {sorted(names)}")


if hasattr(os, "symlink"):
    @t("a broken symlink in the user directory is skipped, not a crash")
    def _broken_symlink_skipped():
        broken_dir = tempfile.mkdtemp(prefix="orgtree-charters-symlink-")
        target = os.path.join(broken_dir, "does-not-exist-target")
        link = os.path.join(broken_dir, "dangling.md")
        os.symlink(target, link)
        p = payload(REPO3, broken_dir)
        names = {c["name"] for c in p["charters"]}
        assert "dangling" not in names, names

    @t("a symlink to a real .md file is followed like an ordinary file")
    def _live_symlink_followed():
        outside_dir = tempfile.mkdtemp(prefix="orgtree-charters-symlink-out-")
        real_file = os.path.join(outside_dir, "real.md")
        w3(outside_dir, "real.md", "reached through a symlink\n")
        link_dir = tempfile.mkdtemp(prefix="orgtree-charters-symlink-in-")
        os.symlink(real_file, os.path.join(link_dir, "linked.md"))
        p = payload(REPO3, link_dir)
        linked = [c for c in p["charters"] if c["name"] == "linked"]
        assert len(linked) == 1, linked
        assert "reached through a symlink" in linked[0]["content"]
else:
    print("  ! §3 symlink checks skipped: os.symlink unavailable on this platform")


if not CRLF_ON_DISK:
    print("\n  ! §2 IS INERT FOR THE CRLF CASE IN THIS CHECKOUT: none of "
          f"{FILES} has CRLF line endings on disk, so these checks would pass "
          "even against a split that cannot handle CRLF. §1 covers it.")
else:
    print(f"\n  §2 is live for the CRLF case: {CRLF_ON_DISK} are CRLF on disk.")

print(f"\nALL {PASS} CHECKS PASS")
