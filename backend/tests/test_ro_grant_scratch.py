"""Read-only folder grants never clamp the agent's own scratch.

A data-root ro grant (given so an agent can READ the live deployment) used to
emit blanket Edit/Write/NotebookEdit deny rules over the whole subtree — the
agent's own working folder included, where its charter requires breadcrumbs
through those very tools (user report 2026-09-01). Run with:
    python backend/tests/test_ro_grant_scratch.py
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import sys
import tempfile
from typing import Any, Callable

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
DATA = tempfile.mkdtemp(prefix="orgtree-ro-scratch-")
os.environ["ORGTREE_DATA"] = DATA
os.makedirs(DATA, exist_ok=True)
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as f:
    f.write('{"net_hub_address":"http://127.0.0.1:9"}')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from orgtree import store, supervisor as S                      # noqa: E402
from orgtree.ledger import USER                                 # noqa: E402

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


def deny_rules(org: Any, nid: str) -> list[str]:
    cmd = S._build_cmd(org, nid, write_ident=False)
    settings = json.loads(cmd[cmd.index("--settings") + 1])
    return list((settings.get("permissions") or {}).get("deny") or [])


def rule(prefix: str, suffix: str, tool: str = "Write") -> str:
    return f"{tool}({prefix.replace(os.sep, '/').rstrip('/')}/{suffix})"


def covers(rules: list[str], path: str) -> bool:
    """Approximate the CLI matcher for OUR OWN emitted shapes: `X/**` covers
    any file below X; `X/*` covers immediate children of X."""
    target = path.replace(os.sep, "/")
    for r in rules:
        if not r.startswith("Write("):
            continue
        pattern = r[len("Write("):-1]
        if pattern.endswith("/**") and target.startswith(pattern[:-2]):
            return True
        if pattern.endswith("/*"):
            base = pattern[:-1]
            rest = target[len(base):] if target.startswith(base) else None
            if rest is not None and rest and "/" not in rest:
                return True
    return False


ORG = store.create_org("zz-ro-scratch")
ORG.hire(USER, None, "haiku", 0, "aya")
SCRATCH = os.path.normpath(S.scratch_dir("zz-ro-scratch", "aya"))
os.makedirs(SCRATCH, exist_ok=True)

# an unrelated read-only grant, and the clamping ancestor grant
PLAIN_RO = os.path.join(DATA, "plain-ro")
os.makedirs(PLAIN_RO, exist_ok=True)
ROOT = os.path.normpath(DATA)          # ancestor of the scratch dir
for name in ("journals", "orgs"):
    os.makedirs(os.path.join(ROOT, name), exist_ok=True)


def with_grants(*dirs: tuple[str, str]) -> list[str]:
    ORG.node("aya")["scope"]["add_dirs"] = [
        {"path": p, "mode": m} for p, m in dirs]
    return deny_rules(ORG, "aya")


def plain_grant_unchanged() -> None:
    rules = with_grants((PLAIN_RO, "ro"))
    for tool in ("Edit", "Write", "NotebookEdit"):
        assert rule(PLAIN_RO, "**", tool) in rules, rules
    eq(len(rules), 3)
    assert covers(rules, os.path.join(PLAIN_RO, "deep", "f.txt"))
    assert not covers(rules, os.path.join(SCRATCH, "breadcrumbs.md"))


check("a read-only grant not covering the scratch keeps its blanket rules",
      plain_grant_unchanged)


def ancestor_grant_carves_scratch() -> None:
    rules = with_grants((ROOT, "ro"))
    blanket = rule(ROOT, "**")
    assert blanket not in rules, "ancestor blanket rule must be carved"
    # the agent's own desk is writable through the whole chain
    assert not covers(rules, os.path.join(SCRATCH, "breadcrumbs.md"))
    assert not covers(rules, os.path.join(SCRATCH, "deep", "notes.md"))
    # everything beside the chain is still denied: root-level files, sibling
    # subtrees at every level, other agents' scratches
    assert covers(rules, os.path.join(ROOT, "defaults.json"))
    assert covers(rules, os.path.join(ROOT, "journals", "warm.jsonl"))
    other = os.path.join(os.path.dirname(SCRATCH), "otheragent", "f.md")
    os.makedirs(os.path.dirname(other), exist_ok=True)
    assert covers(with_grants((ROOT, "ro")), other)


check("an ancestor read-only grant denies everything except the agent's own scratch",
      ancestor_grant_carves_scratch)


def own_scratch_grant_is_noop() -> None:
    eq(with_grants((SCRATCH, "ro")), [])
    rules = with_grants((SCRATCH, "ro"), (PLAIN_RO, "ro"))
    eq(len(rules), 3)
    assert rule(PLAIN_RO, "**") in rules


check("a read-only grant of the agent's own scratch denies nothing",
      own_scratch_grant_is_noop)


def deterministic_render() -> None:
    one = with_grants((ROOT, "ro"), (PLAIN_RO, "ro"))
    two = with_grants((ROOT, "ro"), (PLAIN_RO, "ro"))
    eq(one, two)
    assert one, "expected carved rules"


check("carved rules render deterministically for the identity hash",
      deterministic_render)


def rw_grants_untouched() -> None:
    eq(with_grants((PLAIN_RO, "rw")), [])
    eq(with_grants((ROOT, "rw")), [])


check("read-write grants still emit no deny rules", rw_grants_untouched)

print(f"\nALL {PASS} CHECKS PASS" if not FAIL else
      f"\n{FAIL} FAILED, {PASS} PASSED")
raise SystemExit(1 if FAIL else 0)
