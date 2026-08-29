"""D-181 — the appended system prompt must not churn with live org state.

    .venv/Scripts/python.exe backend/tests/test_prompt_cache_stability.py

No pytest. Plain asserts, `ok N` lines, one final `ALL N CHECKS PASS`.

WHY THIS FILE EXISTS
--------------------
`identity_prompt` is written to `.orgtree-identity.md` and handed to the CLI as
`--append-system-prompt-file` BEFORE EVERY SPAWN. The Anthropic prompt cache is
a strict PREFIX match, and `system` precedes `messages`. So one byte of drift in
that string discards the whole conversation cache and the agent re-pays its
entire context on a resume.

Before the split, that string carried live org state — the chart, the roster,
the credit balance, the org-wide fable lock, the open ask. MEASURED on this
machine 2026-08-29, from the CLI transcripts of 60 nodes / 1,441 resumes:

  · 68% of ALL resumes were cold
  · one hire changed 6 of 8 live agents' system prompts — six bystanders each
    re-paying a full context because a seventh agent was created
  · an org-wide fable_lock toggle hit 8 of 8
  · cold rate tracked how much org state a node rendered: org_visibility
    `self` 33% vs `full` 73%, and that gap survives a gap-length control
    (0% vs 55% cold at sub-60s gaps, so it is not TTL)
  · 196.6M cache-creation tokens on cold resumes vs 2.6M on warm ones

The fix was a RELOCATION, not a removal: `org_state_block` carries the volatile
half and rides the per-turn user envelope, where it costs its own tokens once
and leaves the cached prefix alone.

    §1  the system prompt is inert under other agents' movements
    §2  an agent's OWN scope change still reaches its system prompt
    §3  nothing was lost — every fact still reaches the agent
    §4  the turn envelope actually carries the block
    §5  ⚠ THE CHECKS ABOVE CAN FAIL — value-replacement mutants, each of
        which MUST break §1-§4. A green suite over a dead assertion is the
        failure mode this whole file exists to rule out.

⚠ ISOLATION. Every org here is named `zzpcs-*`, lives under a throwaway
ORGTREE_DATA, and is removed at exit.
"""

from __future__ import annotations

import atexit
import copy
import os
import shutil
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

DATA = tempfile.mkdtemp(prefix="orgtree-pcstest-")
os.environ["ORGTREE_DATA"] = DATA
os.makedirs(DATA, exist_ok=True)
# see test_skills_grant.py: a throwaway data root does NOT isolate the mail hub
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')
os.environ["ORGTREE_PORT"] = "7412"

from orgtree import sandbox as sbx, store, supervisor            # noqa: E402
from orgtree.ledger import USER                                  # noqa: E402

assert DATA != os.path.expanduser("~/orgtree"), "refusing to run on the real data root"
supervisor.chatq_register_org = lambda slug: None
supervisor.chatq_deregister_org = lambda slug: None
sbx.warm = lambda org: None
atexit.register(lambda: shutil.rmtree(DATA, ignore_errors=True))

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


def fixture(name="zzpcs-a", vis="full"):
    """A boss with two reports and a grandchild — enough that a chart exists
    and that hiring a third report is a visible change to somebody."""
    org = store.create_org(name)
    org.hire(USER, None, "opus", 40, "boss")
    org.hire(USER, "boss", "haiku", 8, "alpha")
    org.hire(USER, "boss", "haiku", 8, "beta")
    org.hire(USER, "alpha", "haiku", 0, "gamma")
    for nid in ("boss", "alpha", "beta", "gamma"):
        org.set_scope(USER, nid, org_visibility=vis)
    store.save_org(org)
    return org


def sysprompts(org):
    return {nid: supervisor.identity_prompt(org, nid)
            for nid, n in org.nodes.items() if n["state"] == "live"}


def moved(before, after):
    return sorted(k for k in before if k in after and before[k] != after[k])


# ══════════════════════════════════════ §1 inert under other agents' moves
ORG = fixture()
BASE = sysprompts(ORG)


@t("§1 calibration · re-rendering an UNCHANGED org moves no system prompt")
def _():
    again = sysprompts(store.load_org("zzpcs-a"))
    assert moved(BASE, again) == [], (
        "identity_prompt is not deterministic — every later check in this "
        f"file is meaningless: {moved(BASE, again)}")


@t("§1 a HIRE elsewhere leaves every existing agent's system prompt byte-identical")
def _():
    o = store.load_org("zzpcs-a")
    o.hire(USER, "boss", "haiku", 0, "delta")
    store.save_org(o)
    assert moved(BASE, sysprompts(o)) == [], (
        "hiring one agent rewrote system prompts — every listed agent "
        f"re-pays its whole context next turn: {moved(BASE, sysprompts(o))}")


@t("§1 a RETIRE elsewhere leaves every surviving agent's system prompt byte-identical")
def _():
    o = fixture("zzpcs-ret")
    b = sysprompts(o)
    o.retire(USER, "beta")
    store.save_org(o)
    assert moved(b, sysprompts(o)) == [], moved(b, sysprompts(o))


@t("§1 REALLOCATING credits does not touch any system prompt (not even the parent's)")
def _():
    o = fixture("zzpcs-cred")
    b = sysprompts(o)
    o.reallocate(USER, "alpha", 3)
    store.save_org(o)
    assert moved(b, sysprompts(o)) == [], moved(b, sysprompts(o))


@t("§1 the ORG-WIDE fable lock — the worst offender, 8/8 measured — moves nothing")
def _():
    o = fixture("zzpcs-fable")
    b = sysprompts(o)
    o.d["fable_lock"] = {"at": "2026-08-29T00:00:00Z", "no_reset": True}
    store.save_org(o)
    a = sysprompts(o)
    assert moved(b, a) == [], moved(b, a)
    # and it must still REACH the agents, via the other door
    assert "Fable usage limit is exhausted" in supervisor.org_state_block(o, "boss"), \
        "the fable lock stopped reaching agents altogether"


@t("§1 an OPEN ASK moves no system prompt but does reach the state block")
def _():
    o = fixture("zzpcs-ask")
    b = sysprompts(o)
    o.d.setdefault("asks", []).append(
        {"node": "alpha", "status": "open", "question": "which branch?",
         "at": "2026-08-29T00:00:00Z"})
    store.save_org(o)
    assert moved(b, sysprompts(o)) == [], moved(b, sysprompts(o))
    assert "which branch?" in supervisor.org_state_block(o, "alpha")


# ══════════════════════════════════════ §2 the agent's OWN scope still lands
@t("§2 an agent's own DIRECTORY grant still changes its own system prompt, and "
   "spares an unrelated sibling")
def _():
    o = fixture("zzpcs-own")
    b = sysprompts(o)
    o.set_scope(USER, "alpha",
                add_dirs=[{"path": "C:/zzpcs-granted", "mode": "rw"}])
    store.save_org(o)
    a = sysprompts(o)
    assert "zzpcs-granted" in a["alpha"], "the folder grant left the system prompt"
    assert "alpha" in moved(b, a)
    # ⚠ NOT `== ["alpha"]`, and the reason is measured, not assumed. A dir
    # grant CASCADES in the ledger: the ancestor picks the path up and a
    # descendant is re-clamped to the new ceiling, so `boss` and `gamma` see
    # their OWN grant lines change too. Verified byte-identical on the
    # pre-D-181 checkout (both arms move exactly ['alpha','boss','gamma']), so
    # this is long-standing ledger behaviour and not something the split
    # introduced. Those three are agents whose own scope really did change,
    # which §2 exists to allow. What must NOT move is a BYSTANDER:
    assert "beta" not in moved(b, a), (
        "an unrelated sibling re-pays its whole context because another "
        "agent was granted a folder")


@t("§2 an agent's own CHARTER still changes its own system prompt")
def _():
    o = fixture("zzpcs-chart")
    b = sysprompts(o)
    o.set_scope(USER, "beta", charter="PIN-CHARTER-D181")
    store.save_org(o)
    a = sysprompts(o)
    assert "PIN-CHARTER-D181" in a["beta"], "the charter left the system prompt"
    assert moved(b, a) == ["beta"], moved(b, a)


# ══════════════════════════════════════ §3 nothing was lost
@t("§3 the roster, the chart, the credits and the archived pointer all still reach the agent")
def _():
    o = fixture("zzpcs-loss")
    o.retire(USER, "beta")            # so an archived-hidden pointer exists
    store.save_org(o)
    both = (supervisor.identity_prompt(o, "boss") + "\n"
            + supervisor.org_state_block(o, "boss"))
    for fragment in ("alpha", "gamma",                 # roster + chart
                     "Credits: seat", "grant", "free",  # the credit line
                     "archived here",                   # D-178 pointer
                     "Your superior"):                  # stable half intact
        assert fragment in both, f"{fragment!r} no longer reaches the agent"


@t("§3 the system prompt POINTS to where the live roster went (no silent absence)")
def _():
    p = supervisor.identity_prompt(store.load_org("zzpcs-a"), "boss")
    assert supervisor.ORG_STATE_OPEN in p, (
        "the system prompt names no reports, no peers and no chart, and does "
        "not say where they arrive — an agent will conclude it has none")


# ══════════════════════════════════════ §4 the envelope carries it
@t("§4 the state block is well-formed and delimited")
def _():
    blk = supervisor.org_state_block(store.load_org("zzpcs-a"), "boss")
    assert blk.startswith(supervisor.ORG_STATE_OPEN), blk[:80]
    assert blk.rstrip().endswith(supervisor.ORG_STATE_CLOSE), blk[-80:]
    assert "EARLIER COPIES" in blk, (
        "the block is re-sent every turn, so it must say that older copies "
        "in the conversation are superseded")


@t("§4 _run_one_turn prepends the block to the turn text, ahead of the provider seam")
def _():
    import inspect
    src = inspect.getsource(supervisor._run_one_turn)
    assert "org_state_block(" in src, \
        "the turn path never builds the block — agents would never see it"
    i_block = src.index("state_block + ")
    i_codex = src.index("_codex_leg(")
    assert i_block < i_codex, \
        "the codex lane is reached before the block is attached"
    # D-175: the phantom drop keys off an EMPTY prelude. If the state block
    # were appended into `prelude`, that predicate could never be true again.
    folded = ("prelude.append(state_block" in src
              or "prelude.append(org_state_block" in src)
    assert not folded, (
        "the state block was folded into `prelude` — this silently disables "
        "the D-175 phantom-wake drop")


# ══════════════════════════════════════ §5 the checks can fail
def _mutants():
    """Value replacements against the live functions. Each must break at least
    one check above. A mutant nobody notices means the check is decorative."""
    real_ident = supervisor.identity_prompt
    real_state = supervisor.org_state_block
    o = fixture("zzpcs-mut")
    b = sysprompts(o)
    o2 = store.load_org("zzpcs-mut")
    o2.hire(USER, "boss", "haiku", 0, "epsilon")
    store.save_org(o2)

    results = []

    # ① put the credit balance back into the system prompt
    def ident_with_credits(org, nid, include_archived=False):
        return (real_ident(org, nid, include_archived)
                + f" Credits: free {org.free(nid):g}.")
    supervisor.identity_prompt = ident_with_credits
    results.append(("credits regressed into the system prompt",
                    moved(sysprompts(o), sysprompts(o2)) != []))
    supervisor.identity_prompt = real_ident

    # ② put the roster back into the system prompt
    def ident_with_roster(org, nid, include_archived=False):
        return (real_ident(org, nid, include_archived)
                + " Reports: " + ",".join(org.children(nid)))
    supervisor.identity_prompt = ident_with_roster
    b2 = {k: ident_with_roster(o, k) for k in b}
    a2 = {k: ident_with_roster(o2, k) for k in b}
    results.append(("roster regressed into the system prompt", moved(b2, a2) != []))
    supervisor.identity_prompt = real_ident

    # ③ state block stops carrying the chart
    supervisor.org_state_block = lambda org, nid, include_archived=False: \
        supervisor.ORG_STATE_OPEN + "]\n(nothing)\n" + supervisor.ORG_STATE_CLOSE
    both = (real_ident(o, "boss") + "\n" + supervisor.org_state_block(o, "boss"))
    results.append(("state block stopped carrying the roster",
                    "alpha" not in both))
    supervisor.org_state_block = real_state

    # ④ the system prompt stops pointing at the block
    supervisor.identity_prompt = lambda org, nid, include_archived=False: \
        real_ident(org, nid, include_archived).replace(
            supervisor.ORG_STATE_OPEN, "[SOMEWHERE ELSE")
    results.append(("system prompt stopped naming the state block",
                    supervisor.ORG_STATE_OPEN
                    not in supervisor.identity_prompt(o, "boss")))
    supervisor.identity_prompt = real_ident

    blind = [n for n, detected in results if not detected]
    assert not blind, f"MUTANTS NOT DETECTED — those checks are dead: {blind}"
    return len(results)


_n = _mutants()
check(f"§5 all {_n} value-replacement mutants are DETECTED (the checks above "
      f"can fail)", lambda: None)

print(f"\nALL {PASS} CHECKS PASS")
