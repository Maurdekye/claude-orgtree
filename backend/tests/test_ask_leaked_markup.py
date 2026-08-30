"""D-2xx · orgtree_ask rendered a malformed card to the user (2026-08-30).

WHAT HAPPENED, per the coordinator's report and the org's own stored `asks`
data: two `orgtree_ask` calls from the same agent, same tier, arrived at
`Org.ask_user` with a `question` string whose TAIL was the caller's own
raw, mis-serialized tool call — a closing tag for one of our field names
immediately followed by a `<parameter name="options">` open and the raw
options JSON as literal text — and `options` never arrived as a real
argument at all. Nothing downstream sanitizes `question`: the card renders
whatever string it is given, so the user saw the question text followed by
`</question>`, `<parameter name="options">` and the raw JSON.

A working card from the SAME agent an hour earlier proves the discriminator
is not "long input" (that one was long too) and not "this agent/tier can
never do it" — it is specifically this leaked-tool-call shape, wherever it
comes from upstream of us.

The fix (`Org._recover_leaked_ask`, called from `_norm_question_batch` for
both ask forms): detect the leak, and either recover a clean question plus
the real options by parsing the embedded JSON, or — if that JSON does not
parse, or there's no real question left, or the shape is merely suspicious
without being cleanly recoverable — refuse the call with LedgerError. A
refusal is visible to the calling agent and costs a retry; a silently
mangled card is a bug the user has to notice and report every time.

This file uses the VERBATIM corrupted `question` text pulled from the
org's own `asks` store for the two real incidents (ask ids q3984cfdc and
q3298f61e) — the actual input that broke it, not a paraphrase.

    python backend/tests/test_ask_leaked_markup.py [-v]
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import traceback

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

_TMP = tempfile.mkdtemp(prefix="orgtree-askleak-")
os.environ["ORGTREE_DATA"] = os.path.join(_TMP, "data")
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w",
          encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')
os.environ["USERPROFILE"] = os.environ["HOME"] = os.path.join(_TMP, "home")
os.makedirs(os.environ["HOME"], exist_ok=True)
os.environ["ORGTREE_STEER_HOOK"] = "0"

from orgtree.ledger import LedgerError, Org, USER               # noqa: E402

PASS = 0
FAIL: list[tuple[str, str]] = []
ALL_TOOLS = {"bash": True, "web": True, "edit": True, "subagents": True, "mcp": []}

_n = [0]


def org2():
    _n[0] += 1
    org = Org.create(f"zz askleak {_n[0]}")
    org.hire(USER, None, "opus", 20, "boss")
    org.hire("boss", "boss", "haiku", 5, "kid", add_dirs=[], tools=dict(ALL_TOOLS),
             org_visibility="team", charter="ask-leak test hire")
    return org


def open_asks(org):
    return [a for a in org.d.get("asks", []) if a["status"] == "open"]


def check(label, fn) -> None:
    global PASS
    try:
        fn()
    except Exception:                                            # noqa: BLE001
        FAIL.append((label, traceback.format_exc()))
        print(f"  FAIL     {label}")
        return
    PASS += 1
    print(f"  ok {PASS:3d}  {label}")


# ═══════════════════════════════════ verbatim payloads pulled from the org store

# ask id q3984cfdc, 2026-08-30T18:27:25.002Z — the FIRST mangled call. Real
# stored `question` text: no top-level `options` key existed on this ask at
# all, only this string.
LEAK_1 = (
    "A decision I can't make for you, and it's now blocking the "
    "second-biggest cache fix.\n\n"
    "Every agent is granted one folder per descendant it has — its own "
    "scratch folder, plus one for each agent below it, including retired "
    "ones. My list has 43 entries. That list is regenerated every turn, "
    "and its contents depend on who exists, so hiring an agent anywhere "
    "below you rewrites the prompt of everyone above — which kills their "
    "warm process and makes their next turn cold at roughly 9.7x cost.\n\n"
    "We tried the safe version: drop the retired agents from the list. It "
    "was built, tested, and it turned out to make things WORSE — the "
    "shorter list changes on both hire AND retire, so it roughly doubles "
    "the number of kills to save about 547 tokens a turn. It was not "
    "merged.\n\n"
    "The version that actually works is granting one fixed parent folder "
    "instead of a list of per-agent paths. Then the grant is a constant "
    "and org churn stops invalidating anything. But it widens access: "
    "today an agent can only reach the folders of agents beneath it, and "
    "under this change it could read and write the scratch folders of "
    "agents in other branches, including other orgs on this machine (the "
    "resonite ones).\n\n"
    "Nothing sensitive of yours lives there as far as I know — it's agent "
    "working notes, logs and scratch files. But it's your machine and "
    "your call, not mine.</question>\n"
    '<parameter name="options">[{"label": "Grant the parent folder", '
    '"description": "Take the fix. Agents get read/write access to the '
    "whole scratch area, including other agents' and other orgs' working "
    "folders. The grant becomes a constant, so hires and retires stop "
    'knocking anyone cold. Best available win on this cause."}, {"label": '
    '"Parent folder, read-only", "description": "Same stability win, but '
    "agents can only READ outside their own branch and still write only "
    "their own folder. Slightly more work to implement and I'd need to "
    "confirm it's possible, but it keeps the blast radius of a "
    'misbehaving agent smaller."}, {"label": "Leave it alone", '
    '"description": "Change nothing on this cause. We keep the per-agent '
    "list and accept that every hire knocks the agents above it cold. We "
    "lose this item entirely — the fallback was measured to be worse "
    'than doing nothing."}, {"label": "Orgtree-only parent", '
    '"description": "Grant one fixed folder covering just this org\'s '
    "agents, not the other orgs on the machine. Stable within orgtree, "
    "and the resonite folders stay out of reach. Only works if those "
    'live under separate parents — I\'d have to check."}]'
)

# ask id q3298f61e, 2026-08-30T18:30:01.532Z — the SECOND mangled call, a
# shorter rewrite of the same question that mangled the SAME way.
LEAK_2 = (
    "Sorry — that card came out garbled. Re-asking cleanly.\n\n"
    "Every agent is granted one folder per agent beneath it, including "
    "retired ones. Mine has 43 entries. That list is rebuilt every turn "
    "and depends on who exists, so hiring anyone below you rewrites the "
    "prompt of everyone above, which throws away their cached context. "
    "We have now confirmed this happening in production: one of my turns "
    "re-paid 92,149 tokens with an otherwise byte-identical prompt, "
    "purely because that folder list had changed.\n\n"
    "We built the cautious fix — drop retired agents from the list — and "
    "it measured WORSE than doing nothing, because a shorter list "
    "changes on both hire and retire. It was not merged.\n\n"
    "The only version that helps is granting one fixed parent folder "
    "instead of a per-agent list, so the grant stops depending on who "
    "exists. The cost is wider access: today an agent can only reach "
    "folders of agents beneath it; under this change it could reach "
    "other branches, including the other orgs on this machine.\n\n"
    "What lives there is agent working notes, logs and scratch files. "
    "Your machine, your call.</question>\n"
    '<parameter name="options">[{"label":"Grant parent folder",'
    '"description":"Take the fix. Read and write across the whole '
    "scratch area, including other orgs. The grant becomes constant, so "
    'hires and retires stop knocking anyone cold."},{"label":"Parent, '
    'read-only","description":"Same stability win, but agents can only '
    "read outside their own branch and still write only their own "
    "folder. Smaller blast radius if an agent misbehaves. I would need "
    'to confirm it is implementable."},{"label":"This org only",'
    '"description":"One fixed folder covering only orgtree\'s agents, '
    "leaving the resonite orgs out of reach. Works only if they sit "
    'under separate parents, which I would check first."},{"label":'
    '"Leave it alone","description":"Change nothing here. We keep the '
    "per-agent list and accept that hires knock the agents above them "
    "cold. We lose this item, since the fallback measured worse than "
    'doing nothing."}]'
)


def main() -> int:
    print("orgtree · ask-leaked-tool-call-markup regression")

    print("\n§1  the two real incidents recover cleanly")

    def _leak_1_recovers():
        org = org2()
        r = org.ask_user("boss", LEAK_1)
        assert r.get("asked"), r
        a = open_asks(org)[0]
        assert "</question>" not in a["question"], a["question"]
        assert "<parameter" not in a["question"], a["question"]
        assert a["question"].endswith("your call, not mine."), a["question"]
        assert len(a["options"]) == 4, a.get("options")
        assert a["options"][0]["label"] == "Grant the parent folder"
        assert a["options"][3]["label"] == "Orgtree-only parent"
        assert all("description" in o for o in a["options"])
    check("real incident #1 (q3984cfdc) recovers a clean card", _leak_1_recovers)

    def _leak_2_recovers():
        org = org2()
        r = org.ask_user("boss", LEAK_2)
        assert r.get("asked"), r
        a = open_asks(org)[0]
        assert "</question>" not in a["question"], a["question"]
        assert "<parameter" not in a["question"], a["question"]
        assert a["question"].endswith("Your machine, your call."), a["question"]
        assert len(a["options"]) == 4, a.get("options")
        assert a["options"][0]["label"] == "Grant parent folder"
        assert a["options"][2]["label"] == "This org only"
    check("real incident #2 (q3298f61e) recovers a clean card", _leak_2_recovers)

    def _leak_recovers_in_batch_form():
        org = org2()
        r = org.ask_user("boss", questions=[{"question": LEAK_2, "header": "Folder access"}])
        assert r.get("asked"), r
        a = open_asks(org)[0]
        qd = a["questions"][0]
        assert "<parameter" not in qd["question"], qd["question"]
        assert len(qd["options"]) == 4, qd.get("options")
    check("the leak recovers the same way through the `questions` batch form",
          _leak_recovers_in_batch_form)

    print("\n§2  unrecoverable corruption refuses loudly instead of mangling")

    def _truncated_json_refuses():
        org = org2()
        truncated = LEAK_2[:-40]           # cut mid-way through the last option
        try:
            org.ask_user("boss", truncated)
            raise AssertionError("a truncated embedded-options payload was accepted")
        except LedgerError:
            pass
        assert open_asks(org) == [], "a refused ask must not park a card at all"
    check("truncated embedded JSON is refused, not stored", _truncated_json_refuses)

    def _leak_with_nothing_left_refuses():
        org = org2()
        bare = '</question>\n<parameter name="options">[{"label": "a"}]'
        try:
            org.ask_user("boss", bare)
            raise AssertionError("a leak with no real question text was accepted")
        except LedgerError:
            pass
        assert open_asks(org) == []
    check("a leak with no question left after stripping is refused", _leak_with_nothing_left_refuses)

    def _suspicious_markup_without_recoverable_shape_refuses():
        org = org2()
        # Not the exact recoverable shape (no `</question>` immediately before
        # the parameter tag) — but still unmistakably leaked tool-call syntax,
        # and `options` never arrived structured. Must not be stored verbatim.
        weird = ("What should we do here?\n<parameter name=\"options\">not json"
                 "\n</invoke>")
        try:
            org.ask_user("boss", weird)
            raise AssertionError("unrecoverable leaked markup was accepted verbatim")
        except LedgerError:
            pass
        assert open_asks(org) == []
    check("leaked markup outside the exact recoverable shape still refuses",
          _suspicious_markup_without_recoverable_shape_refuses)

    print("\n§3  ordinary questions are never mistaken for a leak")

    def _plain_question_with_the_word_options_is_unaffected():
        org = org2()
        q = ("What are your options here, and is this a question you can "
             "answer today? No angle brackets anywhere in this one.")
        r = org.ask_user("boss", q, options=["ship", "wait"])
        assert r.get("asked"), r
        a = open_asks(org)[0]
        assert a["question"] == q
        assert a["options"] == [{"label": "ship"}, {"label": "wait"}]
    check("plain prose containing 'options'/'question' is not treated as a leak",
          _plain_question_with_the_word_options_is_unaffected)

    def _structured_options_untouched_when_no_leak():
        org = org2()
        r = org.ask_user("boss", "ship now or wait?",
                         options=[{"label": "ship", "description": "go"}],
                         header="Gate")
        assert r.get("asked"), r
        a = open_asks(org)[0]
        assert a["options"] == [{"label": "ship", "description": "go"}]
    check("normal structured options pass through unchanged", _structured_options_untouched_when_no_leak)

    print()
    if FAIL:
        for label, tb in FAIL:
            print(f"\n✗ {label}\n{tb}")
        print(f"ask-leaked-markup: {PASS} passed · {len(FAIL)} FAILED")
        return 1
    print(f"ask-leaked-markup: all {PASS} checks passed")
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(rc)
