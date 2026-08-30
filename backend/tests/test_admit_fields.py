"""warm.jsonl admit-row field contract (new file per the landing rules).

Run: python tests/test_admit_fields.py

WHY. `handshake_ms` was written as the literal `0` on every admit row,
including cold spawns. cache-misses measured it vacuous across all 46 rows on
2026-08-30. A permanently-constant field in the journal this whole cache effort
steers by is worse than no field: a reader takes `handshake_ms=0` on a cold
spawn as evidence the handshake was free, which is the opposite of what the
audit found. It is removed rather than wired, because this pool by user ruling
never waits for the handshake — there is no interval at this seam to time.

⚠ THE TRAP THIS SUITE IS SHAPED AROUND. "Assert the field is absent" passes
trivially if the row is empty, if the journal never gets written, or if the
whole call silently no-ops (`_journal` swallows OSError by design, so a broken
write is INVISIBLE). Every check below is therefore paired with a control that
fails in exactly that case. An instrument that reports "nothing found" must
first prove it can find something.

CHECKS
  1. an admit row is actually written, and has the keys we depend on
     (the control for everything else — kills the empty-row escape).
  2. `handshake_ms` is absent.
  3. `spawn_ms` is present AND takes different values across rows
     — this is cache-misses' own control, promoted into a test. It is what
     distinguishes "the journal records varying values, and that one field was
     dead" from "the journal records nothing".
  4. no field is constant across rows *except* the ones legitimately constant
     for a single node — a generic net for the next vacuous field, listing
     what it caught so a reader can judge rather than trust.

MUTANTS RUN (value replacements, reverted after):
  M1 re-add `handshake_ms=0` to journal_admit          → check 2 FAILS.
  M2 re-add `handshake_ms=0` AND drop spawn_ms varying
     (force spawn_ms=0)                                → checks 2 and 3 FAIL.
  M3 make `_journal` a no-op (simulating the swallowed
     OSError path)                                     → check 1 FAILS, proving
     checks 2-4 cannot pass by the journal being empty.
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

RIG = tempfile.mkdtemp(prefix="warm-admit-")
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

from orgtree import warmpool as W                       # noqa: E402

PASS = FAIL = 0


def check(label, fn):
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"  ok {PASS:3d}  {label}")
    except Exception as e:                               # noqa: BLE001
        FAIL += 1
        print(f"  FAIL     {label}: {type(e).__name__}: {e}")


# two admissions with deliberately DIFFERENT spawn_ms — the control for check 3
W.journal_admit("s", "n", "sid-1", "cold", "no-process", "h1",
                None, 0, True, slot_wait_s=0.0)
W.journal_admit("s", "n", "sid-2", "warm", "warm-hit", "h1",
                12.5, 483, True, slot_wait_s=0.0)

path = os.path.join(RIG, "journals", "warm.jsonl")
rows = []
if os.path.isfile(path):
    with open(path, encoding="utf-8") as f:
        rows = [json.loads(x) for x in f if x.strip()]
admits = [r for r in rows if r.get("kind") == "admit"]

print("\nwarm.jsonl admit-row field contract")

check("1. CONTROL: admit rows are actually written with the keys we depend on "
      "(without this, checks 2-4 could pass on an empty journal)",
      lambda: (_ for _ in ()).throw(AssertionError(
          f"expected 2 admit rows with session_id/served/spawn_ms, got "
          f"{len(admits)}: {admits}"))
      if not (len(admits) == 2 and all(
          {"session_id", "served", "spawn_ms"} <= set(r) for r in admits))
      else None)

check("2. `handshake_ms` is absent from every admit row",
      lambda: (_ for _ in ()).throw(AssertionError(
          "handshake_ms is back — it was removed as vacuous; do not re-add it "
          "without a proof it can be non-zero"))
      if any("handshake_ms" in r for r in admits) else None)

check("3. CONTROL: `spawn_ms` is present and VARIES across rows "
      "(cache-misses' own control — proves the journal records real values, "
      "so check 2 measured a dead field rather than a dead journal)",
      lambda: (_ for _ in ()).throw(AssertionError(
          f"spawn_ms did not vary: {[r.get('spawn_ms') for r in admits]}"))
      if len({r.get("spawn_ms") for r in admits}) < 2 else None)


def no_new_vacuous_field():
    # legitimately constant for a single node across these two rows.
    # `at` is here for a boring reason rather than a principled one: these two
    # admissions are written microseconds apart and the stamp is millisecond
    # resolution, so it collides in the rig and would flake. It is not vacuous
    # in production.
    allowed = {"kind", "slug", "nid", "ident_hash", "warm_enabled",
               "slot_wait_s", "at"}
    keys = set().union(*(set(r) for r in admits))
    constant = {k for k in keys - allowed
                if len({json.dumps(r.get(k)) for r in admits}) == 1}
    if constant:
        raise AssertionError(
            f"field(s) constant across differing admissions: {sorted(constant)} "
            f"— either make them vary, justify them in `allowed`, or remove "
            f"them. This is the handshake_ms shape.")


check("4. NET: no unexplained field is constant across two differing "
      "admissions", no_new_vacuous_field)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
