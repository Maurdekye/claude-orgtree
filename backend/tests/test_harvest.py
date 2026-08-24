"""Error-harvest suite — OPEN-01 step 1, RECORDING ONLY.

    When the CLI exits nonzero, `err_blob` is built from stderr ALONE, so the
    CLI's own account of the failure — which it puts in its `result` event —
    was thrown away. The operator saw "the CLI exited 1 without writing
    anything to stderr" and could not tell an expired credential from a crash.
    Step 1 carries that reason into the DURABLE RECORD (`last_error` and the
    `turn_error_log` row) and NOWHERE ELSE.

Run:  python backend/tests/test_harvest.py
      --only <sub>   run only sections whose name contains <sub>

No pytest, no network, no model calls, no credentials of any kind.

WHY THE CHECKS ARE SHAPED THE WAY THEY ARE
------------------------------------------
1. THE SEPARATION IS STRUCTURAL, AND THESE CHECKS GUARD THE STRUCTURE — they
   are not the guarantee itself. The widened text cannot reach a
   `_looks_like_*` predicate because it is assembled AT the raise, after every
   predicate has already run, and never assigned back onto `err_blob`. That
   argument decays the moment someone moves a call site, and §2 is what
   notices when it does.

2. §2 READS THE AST, NEVER THE TEXT. This repo has shipped a check that
   matched a name inside a COMMENT rather than in live code. The comments and
   docstrings around this fix necessarily contain `err_blob`,
   `_for_the_record` and `_turn_abandoned`, so a grep-shaped check here would
   match its own documentation and pass while the wiring was wrong. `ast`
   ignores comments by construction.

3. EVERY ABSENCE CHECK HAS A POSITIVE CONTROL. "`_for_the_record` is never
   assigned onto `err_blob`" passes just as happily when the function is not
   called AT ALL — i.e. when the fix was reverted and nothing works. So §2
   also asserts, positively, that it IS wired into the raise. One leg alone is
   an abstention.

4. §3 FAILS LOUDLY AND BY NAME. A separation check that just goes red tends to
   get "fixed" by weakening it, so each failure says WHICH behaviour changed.
"""
from __future__ import annotations

import ast
import os
import sys
import time
import traceback

sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[attr-defined]
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

from orgtree import supervisor as sup                  # noqa: E402

_SUP_SRC_PATH = os.path.abspath(sup.__file__)
with open(_SUP_SRC_PATH, encoding="utf-8") as _f:
    _SUP_SRC = _f.read()
_SUP_AST = ast.parse(_SUP_SRC)

_ARGS = sys.argv[1:]
ONLY = (_ARGS[_ARGS.index("--only") + 1].lower()
        if "--only" in _ARGS and len(_ARGS) > _ARGS.index("--only") + 1 else "")
PASS = 0
FAIL: list[tuple[str, str]] = []
_SECTION = [""]

# The failure shape this fix exists for, MEASURED 2026-08-24 against the
# shipped CLI: a loopback server answering 401 to a FABRICATED key. No real
# credential was involved then and none is involved here — every value below
# is a placeholder chosen to look like the measured shape.
MEASURED_401 = {
    "type": "result", "subtype": "success",       # ⚠ 'success' on a FAILURE
    "is_error": True,
    "result": "Invalid API key · Fix external API key",
    "api_error_status": 401,
    "terminal_reason": "api_error",
    "errors": None,
}
# what the operator actually saw instead, before this fix
PLACEHOLDER = "the CLI exited 1 without writing anything to stderr"


def section(name: str) -> bool:
    _SECTION[0] = name
    if ONLY and ONLY not in name.lower():
        return False
    print(f"\n{name}:")
    return True


def check(label: str, fn) -> None:
    global PASS
    try:
        fn()
    except BaseException:                              # noqa: BLE001
        FAIL.append((f"{_SECTION[0]} / {label}", traceback.format_exc()))
        print(f"  XX     {label}")
        return
    PASS += 1
    print(f"  ok {PASS:3d}  {label}")


def eq(got, want, what="") -> None:
    assert got == want, f"{what}expected {want!r}, got {got!r}"


# ── AST helpers ────────────────────────────────────────────────────────────
def _calls_named(tree, name: str):
    """Every ast.Call in `tree` whose callee is `name`."""
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name) and n.func.id == name]


def _mentions_call(node, name: str) -> bool:
    return bool(_calls_named(node, name))


def _calls_anyhow(node, name: str) -> list[int]:
    """Lines where `name` is CALLED, whether bare (`save_org(...)`) or through
    an attribute (`store.save_org(...)`).

    ⚠ `_calls_named` matches only the bare form. The purity check below used
    it and therefore could not see `store.save_org(...)` — the exact shape the
    real code uses. A mutant adding that call SURVIVED the first mutation
    round, which is the only reason this gap was found rather than shipped.
    """
    out = []
    for n in ast.walk(node):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        if (isinstance(f, ast.Name) and f.id == name) or \
           (isinstance(f, ast.Attribute) and f.attr == name):
            out.append(n.lineno)
    return out


def _assignments_to(tree, target: str):
    """Every assignment statement whose target is the bare name `target`."""
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id == target:
                    out.append(n)
    return out


# ── §0 isolation — the module under test is THIS worktree's ────────────────
def s0_isolation() -> None:
    if not section("§0 isolation — the module under test is this worktree's"):
        return
    # ⚠ PYTHONPATH carries the MAIN tree's backend on this machine. sys.path
    # .insert(0) currently wins, but "wins by import order" is a coincidence,
    # not a property — and importing main's supervisor.py while claiming to
    # test this branch is precisely the abstention that has shipped here
    # before. Assert on the RESOLVED module file. The accounts suite earns
    # this same guarantee separately; it is NOT inherited.
    check("the supervisor under test is THIS worktree's", lambda: (
        None if _SUP_SRC_PATH.startswith(os.path.abspath(
            os.path.join(_HERE, "..")))
        else (_ for _ in ()).throw(AssertionError(
            f"imported {_SUP_SRC_PATH} — not this tree's backend"))))
    check("CONTROL: that guard would reject a foreign path", lambda: (
        None if not "/somewhere/else/supervisor.py".startswith(
            os.path.abspath(os.path.join(_HERE, "..")))
        else (_ for _ in ()).throw(AssertionError("guard matches anything"))))
    check("the harvest helpers exist to be tested at all", lambda: (
        eq((callable(getattr(sup, "_result_detail", None)),
            callable(getattr(sup, "_for_the_record", None))), (True, True))))


# ── §1 the harvest itself ──────────────────────────────────────────────────
def s1_harvest() -> None:
    if not section("§1 the harvest — the CLI's own reason is recovered"):
        return
    check("the measured 401 payload yields its real reason", lambda: (
        eq(sup._result_detail(MEASURED_401),
           "API status 401 · Invalid API key · Fix external API key")))
    check("the recorded error carries the reason, NOT just the placeholder",
          lambda: (
              None if "Invalid API key" in sup._for_the_record(
                  PLACEHOLDER, MEASURED_401)
              else (_ for _ in ()).throw(AssertionError(
                  "the operator would still see only the placeholder — this "
                  "is the whole bug OPEN-01 describes"))))
    check("the ORIGINAL blob survives verbatim (append, never replace)",
          lambda: (
              None if sup._for_the_record(PLACEHOLDER, MEASURED_401).startswith(
                  PLACEHOLDER)
              else (_ for _ in ()).throw(AssertionError(
                  "replacing the blob would drop ECONNRESET/socket-hang-up "
                  "text and silently stop network drops from freezing"))))
    check("api_error_status alone (no result text) still records something",
          lambda: (eq(sup._result_detail(
              {"api_error_status": 401}), "API status 401")))
    check("result text alone (no status) still records something", lambda: (
        eq(sup._result_detail({"result": "boom"}), "boom")))
    check("a clean result event yields NO detail", lambda: (
        eq(sup._result_detail({"type": "result", "subtype": "success"}), "")))
    # ⚠ an empty blob means "no failure" — a manual ⏸ pause clears it. Creating
    # a record here would book a pause as a failure.
    check("an EMPTY blob is never given a record (a ⏸ pause is not a failure)",
          lambda: (eq(sup._for_the_record("", MEASURED_401), "")))
    check("a reason already present is not duplicated", lambda: (
        eq(sup._for_the_record("API status 401 · Invalid API key · Fix "
                               "external API key", MEASURED_401),
           "API status 401 · Invalid API key · Fix external API key")))


# ── §2 the separation, read off the AST ────────────────────────────────────
def _turn_fn():
    """The function holding the terminal raise."""
    for n in ast.walk(_SUP_AST):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for c in _calls_named(n, "_for_the_record"):
                del c
                return n
    return None


def s2_separation() -> None:
    if not section("§2 separation — the widened text cannot reach a predicate"):
        return
    # POSITIVE CONTROL FIRST. Every absence check below passes vacuously if
    # the fix was simply reverted, so prove the wiring exists before proving
    # it is narrow.
    calls = _calls_named(_SUP_AST, "_for_the_record")

    # ⚠ This was TWO checks — "is it wired at all" and "are both doors
    # wired". The first could never fail on its own once the second door
    # existed (>=2 calls implies >=1), so it was an UNKILLABLE check: nothing
    # in the mutation round could ever make it the cause of a failure, which
    # means it was never tested. Merged into one that demands both doors and
    # explains both failure modes.
    def _positive_control():
        if len(calls) >= 2:
            return
        if not calls:
            raise AssertionError(
                "_for_the_record is never called — the fix is not wired at "
                "all, and EVERY absence check in this section is passing "
                "vacuously rather than passing meaningfully.")
        raise AssertionError(
            f"only {len(calls)} recording site carries the CLI's reason. "
            "There are TWO operator-facing doors — the terminal raise and "
            "the retry-exhausted raise — and a door left on the placeholder "
            "is a user still unable to tell an expired credential from a "
            "crash, through a route nobody checked.")
    check("POSITIVE CONTROL: both operator-facing doors are wired",
          _positive_control)

    # RULE 1 — never assigned back onto err_blob, which is classifier input.
    def _rule1():
        bad = [a for a in _assignments_to(_SUP_AST, "err_blob")
               if _mentions_call(a, "_for_the_record")]
        if bad:
            raise AssertionError(
                "BEHAVIOUR CHANGED — CLASSIFICATION: `err_blob` is assigned "
                f"from _for_the_record at line(s) {[a.lineno for a in bad]}. "
                "err_blob is the input to every _looks_like_* predicate, so "
                "this widens what EVERY agent's failures classify as, on "
                "every org — that is step 2's blast radius, not step 1's.")
    check("RULE 1 · the widened text is never assigned onto err_blob", _rule1)

    # RULE 2 — never handed to _turn_abandoned, which puts it into MAIL.
    def _rule2():
        bad = [c for c in _calls_named(_SUP_AST, "_turn_abandoned")
               if any(_mentions_call(a, "_for_the_record") for a in c.args)]
        if bad:
            raise AssertionError(
                "BEHAVIOUR CHANGED — MAIL: _turn_abandoned is being passed "
                f"the widened text at line(s) {[c.lineno for c in bad]}. That "
                "function puts its `err` into MAIL to the agent and drives "
                "its SUPERIOR. Auth-failure text delivered as mail is what "
                "has repeatedly destroyed fable-tier sessions on this "
                "machine — the trigger is the SUBJECT, not any secret.")
    check("RULE 2 · the widened text is never passed to _turn_abandoned",
          _rule2)

    # RULE 3 — no predicate is ever called ON the widened text.
    def _rule3():
        preds = ("_looks_like_usage_limit", "_looks_like_connection_failure",
                 "_looks_like_filtered", "_looks_like_fable_tier_limit")
        bad = []
        for p in preds:
            for c in _calls_named(_SUP_AST, p):
                if any(_mentions_call(a, "_for_the_record") for a in c.args):
                    bad.append((p, c.lineno))
        if bad:
            raise AssertionError(
                "BEHAVIOUR CHANGED — CLASSIFICATION: a predicate is being "
                f"called directly on the widened text: {bad}. Step 1 is "
                "recording only; this is step 2 and needs its own ruling.")
    check("RULE 3 · no _looks_like_* predicate is called on the widened text",
          _rule3)

    # RULE 2b — the OTHER mail site. `_retry_exhausted` mails the agent AND
    # its superior and DRIVES the superior, so it wakes a session. It is a
    # strictly worse place to put auth text than `_turn_abandoned`.
    def _rule2b():
        bad = [c for c in _calls_named(_SUP_AST, "_retry_exhausted")
               if any(_mentions_call(a, "_for_the_record") for a in c.args)]
        if bad:
            raise AssertionError(
                "BEHAVIOUR CHANGED — MAIL THAT WAKES: _retry_exhausted is "
                f"being passed the widened text at line(s) "
                f"{[c.lineno for c in bad]}. It mails the agent AND its "
                "superior and DRIVES the superior. The operator's copy is "
                "the raise beside it, which reaches a screen and no inbox.")
    check("RULE 2b · the widened text is never passed to _retry_exhausted",
          _rule2b)


    # The order argument, mechanically: the raise that carries the widened
    # text must come AFTER the last predicate call, or "assembled after every
    # classifier" is just a claim.
    def _ordering():
        pred_lines = [c.lineno for p in
                      ("_looks_like_usage_limit", "_looks_like_filtered",
                       "_looks_like_connection_failure",
                       "_looks_like_fable_tier_limit", "_died_in_flight")
                      for c in _calls_named(_SUP_AST, p)]
        # predicate calls inside the predicates' own definitions don't count
        pred_lines = [ln for ln in pred_lines if ln > 2000]
        # ⚠ MIN, not max. With max() this check asks only "is the LAST use
        # late", and an added EARLY use — the actual leak — slides straight
        # past it. The property is that EVERY use is after every predicate.
        use = min(c.lineno for c in calls)
        last_pred = max(pred_lines)
        if use < last_pred:
            raise AssertionError(
                "BEHAVIOUR CHANGED — ORDER: the widened text is assembled at "
                f"line {use}, but a classifier still runs at line "
                f"{last_pred}, i.e. AFTER it. The separation argument is "
                "positional; moving the assembly earlier breaks it even if "
                "rules 1-3 still hold.")
    check("ORDER · the widened text is assembled after the last predicate",
          _ordering)


# ── §3 the payload that WOULD match a predicate ────────────────────────────
def s3_trap_payload() -> None:
    if not section("§3 a classifier-matching payload changes no behaviour"):
        return
    # A result payload whose text would MATCH _looks_like_usage_limit. If the
    # widened record ever reached the predicates, this is the shape that would
    # newly freeze a turn that is terminal today.
    trap = {"is_error": True, "api_error_status": 429,
            "result": "Claude usage limit reached · try again in 3 hours"}
    blob = PLACEHOLDER

    check("PREMISE: the trap payload really would match a predicate",
          lambda: (
              None if sup._looks_like_usage_limit(sup._result_detail(trap))
              else (_ for _ in ()).throw(AssertionError(
                  "the trap text does not match _looks_like_usage_limit, so "
                  "this whole section proves nothing — fix the payload"))))
    check("PREMISE: the narrow blob does NOT match that predicate", lambda: (
        None if not sup._looks_like_usage_limit(blob)
        else (_ for _ in ()).throw(AssertionError(
            "the placeholder itself matches — the section cannot "
            "discriminate"))))
    check("the RECORD carries the trap text (recording still works)", lambda: (
        None if "usage limit reached" in sup._for_the_record(blob, trap)
        else (_ for _ in ()).throw(AssertionError("nothing was recorded"))))

    # ⚠ A check lived here that compared `pred(blob)` with `pred(blob)` — a
    # value against ITSELF — and therefore could never fail. It read exactly
    # like a passing separation check while asserting nothing at all. Deleted
    # rather than repaired: what it claimed to prove is genuinely covered by
    # §2's AST rules (the narrow blob is what reaches the predicates) and by
    # the discrimination check below. Left as a comment because the next
    # person will be tempted to write it again.

    def _freeze_would_differ():
        # the honest statement of the hazard: IF the widened text were the
        # classifier input, this turn would newly freeze. That it does not is
        # the property under test.
        narrow = sup._looks_like_usage_limit(blob)
        widened = sup._looks_like_usage_limit(sup._for_the_record(blob, trap))
        if narrow == widened:
            raise AssertionError(
                "this payload does not actually discriminate — narrow and "
                "widened classify the SAME, so a leak would be invisible "
                "here and the section would pass either way")
    check("PREMISE: a leak WOULD be visible (narrow and widened differ)",
          _freeze_would_differ)


# ── §4 _died_in_flight / exit_only are untouched ───────────────────────────
def s4_retry_untouched() -> None:
    if not section("§4 retry behaviour — exit_only and _died_in_flight"):
        return
    # Populating err_blob earlier would leave exit_only False forever and
    # silently disarm the mid-flight retry (the code's own placement rule 1).
    # This fix never touches err_blob; these pin the predicate's truth table
    # so a later "tidy-up" cannot quietly change it.
    check("died_in_flight: exit_only+started+no boundary → retry", lambda: (
        eq(sup._died_in_flight(exit_only=True, started=True, boundary=False),
           True)))
    check("died_in_flight: a boundary result prevents the retry", lambda: (
        eq(sup._died_in_flight(exit_only=True, started=True, boundary=True),
           False)))
    check("died_in_flight: something explained why → no retry", lambda: (
        eq(sup._died_in_flight(exit_only=False, started=True, boundary=False),
           False)))

    def _no_new_writes():
        # _for_the_record must be a PURE function of its arguments: it may not
        # write state, mail, or the org doc. Anything else is blast radius.
        fn = None
        for n in ast.walk(_SUP_AST):
            if isinstance(n, ast.FunctionDef) and n.name == "_for_the_record":
                fn = n
        if fn is None:
            raise AssertionError("_for_the_record is gone")
        for bad in ("save_org", "load_org", "notify", "_log_turn_error",
                    "_turn_abandoned", "mark_unrecoverable"):
            hits = _calls_anyhow(fn, bad)
            if hits:
                raise AssertionError(
                    f"BEHAVIOUR CHANGED — SIDE EFFECT: _for_the_record calls "
                    f"{bad} at line(s) {hits}; step 1 is display and logging "
                    f"only, and a write here would give the recording path a "
                    f"blast radius it is specifically not allowed to have")
    check("_for_the_record is pure — no doc writes, no mail, no notify",
          _no_new_writes)


def main() -> None:
    t0 = time.perf_counter()
    print(f"supervisor under test: {_SUP_SRC_PATH}")
    for fn in (s0_isolation, s1_harvest, s2_separation, s3_trap_payload,
               s4_retry_untouched):
        fn()
    dt = time.perf_counter() - t0
    print()
    if FAIL:
        print("=" * 72)
        for label, tb in FAIL:
            print(f"\nFAILED: {label}\n{tb}")
        print("=" * 72)
        print(f"{PASS} checks passed, {len(FAIL)} FAILED  ({dt:.1f}s)")
        sys.exit(1)
    print(f"ALL {PASS} CHECKS PASS  ({dt:.1f}s)")


if __name__ == "__main__":
    main()
