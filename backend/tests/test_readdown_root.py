"""D-201/S2(a) — the read-down is ONE fixed path, so org churn cannot move the
spawn argv (new file per the landing rules).

Run: python tests/test_readdown_root.py

WHY. The read-down used to emit one `--add-dir` per descendant. That list is in
the argv, the argv is in `warmpool.ident_hash`, and the CLI renders it into its
base prompt as "Additional working directories" — ahead of our appended prompt.
So a hire anywhere in a subtree re-paid the prefix of the parent and every
ancestor. Caught in production: a coordinator turn-pair with a byte-identical
appended prompt, read 20,888 / create 92,149, `argv:add_dir` the only mover.

THE CLAIM UNDER TEST IS AN INVARIANCE, and invariance checks are the easiest
kind to pass vacuously — "nothing changed" is also what you get if the thing
never had any content, if both sides are empty, or if the comparison is
between two copies of one value. Every check below is therefore paired with a
control that fails in exactly that case:

  1. the root IS granted, and the agent's own + a report's scratch are under it
     (control: the grant is not empty and actually covers what it replaced).
  2. argv is BYTE-IDENTICAL across a HIRE.
  3. argv is BYTE-IDENTICAL across a RETIRE.
     ⚠ 3 is the one the abandoned fix (b) could not pass — pruning archived
     entries makes retire a change event. See commit 2e0eb47.
  4. ident_hash is unchanged across both.
  5. CONTROL FOR 2-4: a genuine identity change (charter edit) DOES move the
     hash. Without this, checks 2-4 pass even if ident_hash returned a
     constant, which is the exact failure that would make this suite useless.
  6. CONTROL FOR 2-3: the argv is non-empty and contains --add-dir at all.
  7. exactly ONE --add-dir names a scratch path, not one per node — the
     coordinator's vacuity check: if the CLI or this code expanded the root to
     a list, the fix would do nothing.

MUTANTS RUN (value replacements, reverted after):
  M1 restore the per-descendant loop (the pre-change code)
        → checks 2, 3, 4, 7 FAIL (argv moves on both hire and retire).
  M2 restore the loop AND prune archived (the abandoned fix (b))
        → check 3 FAILS, check 2 FAILS; demonstrates (b) fixes nothing here.
  M3 `ident_hash` forced to a constant      → check 5 FAILS.
  M4 drop the root grant entirely           → checks 1, 6, 7 FAIL.
"""
import io
import json
import os
import sys
import tempfile

if not getattr(sys, "_utf8_wrapped", False):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace")
    sys._utf8_wrapped = True

RIG = tempfile.mkdtemp(prefix="d201-s2a-")
HOME = os.path.join(RIG, "home")
os.makedirs(HOME, exist_ok=True)
BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

os.environ["ORGTREE_DATA"] = RIG
os.environ["HOME"] = HOME
os.environ["USERPROFILE"] = HOME
os.environ["ORGTREE_WARM"] = "0"
sys.path.insert(0, BACKEND)

with open(os.path.join(RIG, "defaults.json"), "w", encoding="utf-8") as f:
    f.write('{"net_hub_address": "http://127.0.0.1:9"}')

from orgtree import store, supervisor as S, warmpool as W   # noqa: E402
from orgtree.ledger import USER                             # noqa: E402

PASS = FAIL = 0


def check(label, fn):
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"  ok {PASS:3d}  {label}")
    except Exception as e:                                   # noqa: BLE001
        FAIL += 1
        print(f"  FAIL     {label}: {type(e).__name__}: {e}")


org = store.create_org("d201 s2a rig")
SLUG = org.d["slug"]
for parent, name in ((None, "boss"), ("boss", "mid"), ("mid", "leaf")):
    org.hire(USER, parent, "haiku", 6, name, add_dirs=[], tools={"mcp": []},
             org_visibility="full", charter="c")
store.save_org(org)


def reload_org():
    return store.load_org(SLUG)


def argv(nid, o=None):
    return S._build_cmd(o or reload_org(), nid, write_ident=False)


def add_dirs(nid, o=None):
    c = argv(nid, o)
    return [c[i + 1] for i, a in enumerate(c) if a == "--add-dir"]


def ihash(nid):
    return W.ident_hash(reload_org(), nid)


print("\nD-201/S2(a) read-down — one fixed path, argv invariant to org churn")

root = os.path.dirname(S.scratch_dir(SLUG, "boss"))

check("1. the scratch ROOT is granted, and both the agent's own folder and a "
      "report's folder live under it (control: the grant covers what the "
      "per-descendant list used to)",
      lambda: (_ for _ in ()).throw(AssertionError(
          f"root {root!r} not granted, or does not cover the nodes: "
          f"{add_dirs('boss')}"))
      if not (any(os.path.realpath(p) == os.path.realpath(root)
                  for p in add_dirs("boss"))
              and os.path.realpath(S.scratch_dir(SLUG, "leaf")).startswith(
                  os.path.realpath(root) + os.sep))
      else None)

check("6. CONTROL: the argv is non-empty and does contain --add-dir "
      "(so the invariance checks are not comparing two empty lists)",
      lambda: (_ for _ in ()).throw(AssertionError(
          f"no --add-dir in argv: {argv('boss')}"))
      if not add_dirs("boss") else None)


def one_scratch_entry():
    scratch_entries = [p for p in add_dirs("boss")
                       if os.path.realpath(p).startswith(
                           os.path.realpath(os.path.dirname(root)))]
    if len(scratch_entries) != 1:
        raise AssertionError(
            f"expected exactly ONE scratch --add-dir (the root), got "
            f"{len(scratch_entries)}: {scratch_entries} — if this is a list, "
            f"the fix is vacuous")


check("7. exactly ONE --add-dir names a scratch path (the coordinator's "
      "vacuity check: a list here would mean the fix does nothing)",
      one_scratch_entry)

# ── 2 + 4: a HIRE must not move the argv or the hash ──────────────────────
before_argv, before_hash = argv("boss"), ihash("boss")
o = reload_org()
o.hire(USER, "leaf", "haiku", 2, "newbie", add_dirs=[], tools={"mcp": []},
       org_visibility="full", charter="c")
store.save_org(o)
after_hire_argv, after_hire_hash = argv("boss"), ihash("boss")

check("2. argv is BYTE-IDENTICAL across a HIRE two levels below",
      lambda: (_ for _ in ()).throw(AssertionError(
          "argv moved on a hire:\n  before: "
          + json.dumps([p for p in before_argv if "scratch" in p])
          + "\n  after:  "
          + json.dumps([p for p in after_hire_argv if "scratch" in p])))
      if before_argv != after_hire_argv else None)

# ── 3: a RETIRE must not move it either — fix (b) could not pass this ─────
o = reload_org()
o.retire(USER, "newbie")
store.save_org(o)
after_ret_argv, after_ret_hash = argv("boss"), ihash("boss")

check("3. argv is BYTE-IDENTICAL across a RETIRE "
      "(the check the abandoned fix (b) fails)",
      lambda: (_ for _ in ()).throw(AssertionError(
          "argv moved on a retire:\n  before: "
          + json.dumps([p for p in after_hire_argv if "scratch" in p])
          + "\n  after:  "
          + json.dumps([p for p in after_ret_argv if "scratch" in p])))
      if after_hire_argv != after_ret_argv else None)

check("4. ident_hash is unchanged across BOTH the hire and the retire",
      lambda: (_ for _ in ()).throw(AssertionError(
          f"hash moved: {before_hash} -> {after_hire_hash} -> {after_ret_hash}"))
      if not (before_hash == after_hire_hash == after_ret_hash) else None)

# ── 5: CONTROL — a real identity change MUST still move the hash ──────────
o = reload_org()
o.nodes["boss"]["charter"] = "a materially different charter"
store.save_org(o)

check("5. CONTROL: a charter edit DOES move the hash (without this, checks "
      "2-4 would pass even if ident_hash were a constant)",
      lambda: (_ for _ in ()).throw(AssertionError(
          "hash did NOT move on a charter edit — ident_hash is insensitive "
          "and checks 2-4 prove nothing"))
      if ihash("boss") == after_ret_hash else None)

print(f"\n  boss --add-dir entries: {add_dirs('boss')}")
print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
