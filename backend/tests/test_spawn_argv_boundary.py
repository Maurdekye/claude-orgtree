"""D-218: settings ride argv for identity, and a file for the OS.

A broad read-only ancestor grant renders per-sibling deny rules (D-217) with
no bound: live-hit 2026-09-01, ~690 rules, a 54,056-char command line, and
every spawn on the machine dead with [WinError 206] before provider contact.
`spawn_argv` parks the inline `--settings` JSON in a scratch dotfile at the
exec boundary; identity code upstream keeps hashing the inline form. Run with:
    python backend/tests/test_spawn_argv_boundary.py
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Callable

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
DATA = tempfile.mkdtemp(prefix="orgtree-spawn-argv-")
os.environ["ORGTREE_DATA"] = DATA
os.makedirs(DATA, exist_ok=True)
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as f:
    f.write('{"net_hub_address":"http://127.0.0.1:9"}')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from orgtree import store, supervisor as S, warmpool as W        # noqa: E402
from orgtree.ledger import USER                                  # noqa: E402

assert DATA != os.path.expanduser("~/orgtree")
S.chatq_register_org = lambda slug: None
S.chatq_deregister_org = lambda slug: None
atexit.register(lambda: shutil.rmtree(DATA, ignore_errors=True))

PASS = FAIL = 0


def check(label: str, fn: Callable[[], None]) -> None:
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"  ok {PASS:2d}  {label}")
    except Exception as exc:
        FAIL += 1
        print(f"  FAIL    {label}: {exc}")
        import traceback
        traceback.print_exc()


def eq(got: Any, want: Any) -> None:
    assert got == want, f"got {got!r}; want {want!r}"


ORG = store.create_org("zz-spawn-argv")
ORG.hire(USER, None, "haiku", 0, "aya")
SCRATCH = os.path.normpath(S.scratch_dir("zz-spawn-argv", "aya"))
os.makedirs(SCRATCH, exist_ok=True)

# the killer config: a broad read-only ANCESTOR grant over a root with many
# siblings — every sibling at every chain level becomes three deny rules
ROOT = os.path.normpath(DATA)
# 700 siblings: enough that even D-220's one-Edit-rule-per-path render
# exceeds the 32,767-char cap inline, so the boundary stays proven
for i in range(700):
    os.makedirs(os.path.join(ROOT, f"sibling-directory-{i:04d}"), exist_ok=True)
ORG.node("aya")["scope"]["add_dirs"] = [{"path": ROOT, "mode": "ro"}]

CMD = S._build_cmd(ORG, "aya", write_ident=False)
INLINE = CMD[CMD.index("--settings") + 1]


def inline_form_is_the_proven_danger() -> None:
    assert INLINE.lstrip().startswith("{"), "identity form must stay inline"
    total = len(subprocess.list2cmdline(CMD))
    assert total > 32_767, (
        f"fixture too small to prove the overflow ({total} chars) — grow the "
        f"sibling count")
    deny = json.loads(INLINE)["permissions"]["deny"]
    assert len(deny) > 600, f"expected an unbounded render, got {len(deny)}"


check("the inline identity form really exceeds the Windows argv cap",
      inline_form_is_the_proven_danger)


def exec_form_fits_and_matches() -> None:
    out = S.spawn_argv(ORG, "aya", CMD)
    val = out[out.index("--settings") + 1]
    assert not val.lstrip().startswith("{"), "exec form must carry a path"
    assert os.path.isfile(val), val
    eq(os.path.dirname(os.path.normpath(val)), SCRATCH)
    with open(val, encoding="utf-8") as f:
        eq(json.load(f), json.loads(INLINE))
    total = len(subprocess.list2cmdline(out))
    assert total < 32_767, f"exec argv still over the cap: {total}"
    # everything except the settings value is untouched, in order
    eq([a for a in out if a != val], [a for a in CMD if a != INLINE])


check("the exec form fits under the cap and carries the same bytes",
      exec_form_fits_and_matches)


def identity_input_is_not_mutated() -> None:
    before = list(CMD)
    S.spawn_argv(ORG, "aya", CMD)
    eq(CMD, before)
    # D-201: the warm identity keeps hashing the INLINE form
    norm = W._argv_normalized(CMD)
    assert INLINE in norm, "identity argv lost the settings bytes"


check("spawn_argv never mutates the identity argv", identity_input_is_not_mutated)


def rewrite_self_heals() -> None:
    out = S.spawn_argv(ORG, "aya", CMD)
    path = out[out.index("--settings") + 1]
    with open(path, "w", encoding="utf-8") as f:
        f.write('{"tampered": true}')
    out2 = S.spawn_argv(ORG, "aya", CMD)
    eq(out2[out2.index("--settings") + 1], path)
    with open(path, encoding="utf-8") as f:
        eq(json.load(f), json.loads(INLINE))


check("a tampered settings file is rewritten before the next spawn",
      rewrite_self_heals)


def keepalive_gets_its_own_file() -> None:
    turn = S.spawn_argv(ORG, "aya", CMD)
    keep = S.spawn_argv(ORG, "aya", CMD, purpose="keepalive")
    tpath = turn[turn.index("--settings") + 1]
    kpath = keep[keep.index("--settings") + 1]
    assert tpath != kpath, "keepalive must not race the turn's file"
    assert os.path.isfile(tpath) and os.path.isfile(kpath)


check("a keepalive spawn parks settings in its own file",
      keepalive_gets_its_own_file)


def absent_or_path_settings_pass_through() -> None:
    bare = ["claude", "-p", "--model", "x"]
    eq(S.spawn_argv(ORG, "aya", bare), bare)
    already = ["claude", "--settings", os.path.join(SCRATCH, "s.json")]
    eq(S.spawn_argv(ORG, "aya", already), already)


check("argv without inline settings passes through unchanged",
      absent_or_path_settings_pass_through)


def guard_names_the_overflow() -> None:
    monster = ["claude", "--settings", "{}", "--pad", "y" * 33_000]
    if os.name != "nt":
        S.spawn_argv(ORG, "aya", monster)      # guard is Windows-only
        return
    try:
        S.spawn_argv(ORG, "aya", monster)
    except RuntimeError as e:
        assert "32,767" in str(e), str(e)
        assert "largest element" in str(e), str(e)
    else:
        raise AssertionError("an over-cap argv must refuse in writing")


check("an over-cap argv refuses with a named error, not [WinError 206]",
      guard_names_the_overflow)

print(f"\nALL {PASS} CHECKS PASS" if not FAIL else
      f"\n{FAIL} FAILED, {PASS} PASSED")
raise SystemExit(1 if FAIL else 0)
