"""Name the cause when the CLI dies with no result event to read.

THE INCIDENT (user, 2026-08-25). A haiku seat's first turn died. The operator
was shown, in full:

    the CLI exited 1 without writing anything to stderr

The real reason was in the CLI's own transcript the whole time — "Failed to
authenticate: OAuth session expired and could not be refreshed" — and the user
had to work it out and re-login by hand. That message is worse than silence: it
reads like an orgtree bug, so it points away from the one thing that would fix
it.

WHY THE EXISTING MACHINERY COULD NOT SEE IT
-------------------------------------------
`_result_detail` and `_looks_like_auth_failure` both read the RESULT EVENT —
deliberately, because a number cannot accidentally contain "usage limit
reached" (see their docstrings; that design is not being touched). But this
failure produced NO RESULT EVENT AT ALL and no HTTP status: the OAuth refresh
failed locally, before any authenticated request went out. Every reader of
`res` abstained, and an abstention reads exactly like "nothing was wrong".

So the fix reads the STREAM, and the load-bearing property is that it is a
FALLBACK and not an override: consulted only when the result event said
nothing.

    §1  the capture helpers
    §2  the incident, end to end — and the control showing the old behaviour
    §3  fallback, never override
    §4  RECORDING ONLY — structural, mutant-resistant
    §5  controls: what would make the above vacuous

    python backend/tests/test_auth_cause.py
"""
from __future__ import annotations

import ast
import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="orgtree-authcause-")
os.environ["ORGTREE_DATA"] = _TMP
os.makedirs(_TMP, exist_ok=True)
with open(os.path.join(_TMP, "defaults.json"), "w", encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from orgtree import supervisor as sup  # noqa: E402

_SRC_PATH = os.path.join(os.path.dirname(__file__), "..", "orgtree",
                         "supervisor.py")
with open(_SRC_PATH, encoding="utf-8") as _f:
    _SRC = _f.read()
_AST = ast.parse(_SRC)

FAILS: list[str] = []
CHECKS = 0

# The exact shapes measured 2026-08-25. Nothing here is invented: the retry
# event came off the stdout stream of the shipped CLI (loopback 401 +
# fabricated key, no real credential), the synthetic assistant message came
# out of the failing session's own transcript.
RETRY_EVENT = {"type": "system", "subtype": "api_retry",
               "error": "authentication_failed"}
EXPIRED_TEXT = ("Failed to authenticate: OAuth session expired and could not "
                "be refreshed")
PLACEHOLDER = "the CLI exited 1 without writing anything to stderr"


def check(label: str, cond: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if cond:
        print(f"ok {CHECKS:3d}  {label}")
    else:
        print(f"FAIL      {label}" + (f"  — {detail}" if detail else ""))
        FAILS.append(label + (f" — {detail}" if detail else ""))


# --------------------------------------------------------------------------
def s1_capture() -> None:
    print("\n§1  the capture helpers")
    d: dict[str, str] = {}
    sup._note_api_error(d, RETRY_EVENT["error"], None)
    check("1.1 a typed code with no text is still captured",
          d.get("code") == "authentication_failed", repr(d))

    # FIRST WINS — retries repeat, and the first is the cause, not the echo
    sup._note_api_error(d, "server_error", "something else entirely")
    check("1.2 a later error does not overwrite the first",
          d.get("code") == "authentication_failed", repr(d))

    d2: dict[str, str] = {}
    sup._note_api_error(d2, "", "")
    check("1.3 an empty error records nothing at all", d2 == {}, repr(d2))

    d3: dict[str, str] = {}
    sup._note_api_error(d3, "authentication_failed", EXPIRED_TEXT)
    got = sup._stream_error_detail(d3)
    check("1.4 the human sentence leads, the typed code rides along",
          EXPIRED_TEXT in got and "[authentication_failed]" in got, repr(got))
    check("1.5 …and it says what the human must DO",
          "claude auth login" in got, repr(got))
    check("1.6 …and that no amount of retrying helps",
          "retrying" in got.lower(), repr(got))

    # a NON-auth API error must not wear the auth label
    d4: dict[str, str] = {}
    sup._note_api_error(d4, "server_error", "upstream exploded")
    got4 = sup._stream_error_detail(d4)
    check("1.7 a non-auth code is reported without the auth label",
          "upstream exploded" in got4
          and "AUTHENTICATION FAILURE" not in got4, repr(got4))
    check("1.8 nothing captured ⇒ nothing rendered",
          sup._stream_error_detail({}) == "", repr(sup._stream_error_detail({})))


# --------------------------------------------------------------------------
def s2_incident() -> None:
    print("\n§2  the incident, end to end")
    # exactly what the harness held that day: the generic placeholder, and an
    # EMPTY result event — the CLI never emitted one
    cap: dict[str, str] = {}
    sup._note_api_error(cap, RETRY_EVENT["error"], None)
    rec = sup._for_the_record(PLACEHOLDER, {}, cap)

    check("2.1 the record still contains the original blob verbatim",
          PLACEHOLDER in rec, repr(rec))
    check("2.2 …and now NAMES the cause as an authentication failure",
          "AUTHENTICATION FAILURE" in rec, repr(rec))
    check("2.3 …and tells the reader the remedy",
          "claude auth login" in rec, repr(rec))

    # ⚠ THE CONTROL. Without it, §2.1-2.3 are equally explained by a
    # `_for_the_record` that appends that label to EVERYTHING. This is the
    # pre-fix call — same blob, same empty result, no stream capture — and it
    # must still produce the bare placeholder and nothing else.
    old = sup._for_the_record(PLACEHOLDER, {})
    check("2.4 CONTROL — with no stream capture it is the bare placeholder "
          "(the old behaviour, unchanged)",
          old == PLACEHOLDER, repr(old))
    check("2.5 CONTROL — so the label came from the capture, not from the blob",
          "AUTHENTICATION FAILURE" not in old, repr(old))

    # the transcript carrier, which I did NOT confirm in the stream: the same
    # typed field on a synthetic assistant message. Both are read, so a change
    # in which one the CLI emits cannot silently blind this.
    cap2: dict[str, str] = {}
    sup._note_api_error(cap2, "authentication_failed", EXPIRED_TEXT)
    rec2 = sup._for_the_record(PLACEHOLDER, {}, cap2)
    check("2.6 the assistant-message carrier reports the CLI's own sentence",
          EXPIRED_TEXT in rec2, repr(rec2))


# --------------------------------------------------------------------------
def s3_fallback_not_override() -> None:
    print("\n§3  the stream is a FALLBACK, never an override")
    # a REAL result event: the 401 shape measured 2026-08-24
    res = {"is_error": True, "result": "Invalid API key · Fix external API key",
           "api_error_status": 401, "terminal_reason": "api_error"}
    cap: dict[str, str] = {}
    sup._note_api_error(cap, "server_error", "a stale earlier retry")
    rec = sup._for_the_record(PLACEHOLDER, res, cap)
    check("3.1 the CLI's own result wins when it exists",
          "Invalid API key" in rec, repr(rec))
    check("3.2 …and the stale stream capture is NOT appended",
          "a stale earlier retry" not in rec, repr(rec))
    check("3.3 …and the 401 path still names the auth failure itself",
          "AUTHENTICATION FAILURE" in rec, repr(rec))

    # rule 3 of `_for_the_record`: NEVER CREATE a record. An empty blob means
    # "no failure" — a manual pause clears it — so a capture must not conjure
    # one, or a ⏸ becomes a failure.
    cap3: dict[str, str] = {}
    sup._note_api_error(cap3, "authentication_failed", EXPIRED_TEXT)
    check("3.4 an empty err_blob stays empty even with a capture in hand",
          sup._for_the_record("", {}, cap3) == "",
          repr(sup._for_the_record("", {}, cap3)))


# --------------------------------------------------------------------------
def s4_recording_only() -> None:
    print("\n§4  RECORDING ONLY — structural")
    # The same property test_harvest §7 pins for `_looks_like_auth_failure`,
    # applied to the NEW discovery route. Without this, someone could wire the
    # stream capture into a freeze or a re-route and trip nothing: the old
    # check watches the predicate, and this path never calls it.
    BEHAVIOUR = ("_turn_abandoned", "_retry_exhausted", "notify",
                 "fable_filter_hit", "mark_unrecoverable", "_bump_hard_fail",
                 "resume_frozen", "record_limit", "redrive_after_limit",
                 "freeze_for_limit")
    NAMES = ("stream_api_err", "_stream_error_detail", "_note_api_error")

    def mentions(node) -> bool:
        return any(isinstance(n, ast.Name) and n.id in NAMES
                   or isinstance(n, ast.Attribute) and n.attr in NAMES
                   for n in ast.walk(node))

    parent = {}
    for n in ast.walk(_AST):
        for c in ast.iter_child_nodes(n):
            parent[c] = n

    bad = []
    for n in ast.walk(_AST):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        nm = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
        if nm not in BEHAVIOUR:
            continue
        if mentions(n):
            bad.append((nm, n.lineno, "the capture is inside the call"))
            continue
        cur = n
        while cur in parent:
            up = parent[cur]
            if isinstance(up, ast.IfExp) and mentions(up.test):
                bad.append((nm, n.lineno, "a conditional gates it"))
                break
            if isinstance(up, ast.If) and mentions(up.test):
                bad.append((nm, n.lineno, "an `if` gates it"))
                break
            if isinstance(up, (ast.FunctionDef, ast.AsyncFunctionDef)):
                break
            cur = up
    check("4.1 no freeze/retry/mail/routing path is driven by the capture",
          not bad, repr(bad))

    # ⚠ AND IT MUST NEVER BECOME A CAPACITY MARK. An auth break has no reset
    # time; writing one into `usage_refreshes` makes the lane silently return,
    # still broken, and die again — flapping that reads as a router bug.
    src_of = {}
    for n in ast.walk(_AST):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            src_of[n.name] = n
    marks = [n.lineno for n in ast.walk(_AST)
             if isinstance(n, ast.Call)
             and getattr(n.func, "attr", "") == "record_limit"
             and mentions(n)]
    check("4.2 the capture never reaches `accounts.record_limit`",
          not marks, repr(marks))

    # rule 2 on `_for_the_record`: the widened text must not become MAIL.
    # `_turn_abandoned` / `_retry_exhausted` take the NARROW blob.
    mailed = []
    for nm in ("_turn_abandoned", "_retry_exhausted"):
        for c in [n for n in ast.walk(_AST) if isinstance(n, ast.Call)
                  and (getattr(n.func, "id", "") == nm
                       or getattr(n.func, "attr", "") == nm)]:
            for a in c.args + [k.value for k in c.keywords]:
                for sub in ast.walk(a):
                    if isinstance(sub, ast.Call) and getattr(
                            sub.func, "id", "") in ("_for_the_record",
                                                    "_stream_error_detail"):
                        mailed.append((nm, c.lineno))
    check("4.3 the widened text never becomes mail to the agent",
          not mailed, repr(mailed))


# --------------------------------------------------------------------------
def s5_controls() -> None:
    print("\n§5  controls — what would make the above vacuous")
    # 5.1 the structural check can FAIL. A §4 that cannot fire proves nothing,
    #     and this subtree has shipped exactly that more than once.
    probe = ast.parse("def f():\n"
                      "    if stream_api_err:\n"
                      "        _turn_abandoned(a, b, c, d)\n")
    parent = {}
    for n in ast.walk(probe):
        for c in ast.iter_child_nodes(n):
            parent[c] = n
    found = False
    for n in ast.walk(probe):
        if isinstance(n, ast.Call) and getattr(n.func, "id", "") == \
                "_turn_abandoned":
            cur = n
            while cur in parent:
                up = parent[cur]
                if isinstance(up, ast.If) and any(
                        isinstance(x, ast.Name) and x.id == "stream_api_err"
                        for x in ast.walk(up.test)):
                    found = True
                    break
                if isinstance(up, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    break
                cur = up
    check("5.1 the §4 walk DOES catch a wired capture (it can fail)", found)

    # 5.2 the capture is actually WIRED IN — a helper nothing calls would pass
    #     every behavioural check above while changing nothing in production
    calls = [n.lineno for n in ast.walk(_AST)
             if isinstance(n, ast.Call)
             and getattr(n.func, "id", "") == "_note_api_error"]
    check("5.2 `_note_api_error` is called from the stream loop", len(calls) >= 2,
          f"{len(calls)} call site(s): {calls}")
    passed = [n.lineno for n in ast.walk(_AST)
              if isinstance(n, ast.Call)
              and getattr(n.func, "id", "") == "_for_the_record"
              and len(n.args) >= 3]
    check("5.3 …and every `_for_the_record` recording site is given it",
          len(passed) >= 2, f"{len(passed)} site(s) pass a capture: {passed}")

    # 5.4 the api_retry subtype is matched EXACTLY as measured — a typo there
    #     ("api-retry") would silently capture nothing forever, which is the
    #     abstention shape: no error, no output, and a machine that looks
    #     perfectly healthy while telling the user nothing.
    #
    # ⚠ THIS CHECK WAS WRITTEN AS `'"api_retry"' in _SRC` AND THE MUTANT
    # SURVIVED. The literal also appears in the COMMENT above the branch, so a
    # substring search over the source passed while the live comparison read
    # "api-retry" and matched nothing — this subtree's signature failure,
    # caught here only because the mutation round was actually run. Assert on
    # the COMPARISON NODE, which no amount of prose can satisfy.
    live = []
    for n in ast.walk(_AST):
        if not isinstance(n, ast.Compare):
            continue
        # ev.get("subtype") == "api_retry"
        left = n.left
        if not (isinstance(left, ast.Call)
                and getattr(left.func, "attr", "") == "get"
                and left.args and isinstance(left.args[0], ast.Constant)
                and left.args[0].value == "subtype"):
            continue
        for c in n.comparators:
            if isinstance(c, ast.Constant) and c.value == "api_retry":
                live.append(n.lineno)
    check("5.4 live code compares subtype to the measured 'api_retry' "
          "(asserted on the AST — a comment cannot satisfy it)",
          bool(live), "no such comparison in supervisor.py")

    # 5.5 …and the auth family is the measured vocabulary, not invented
    check("5.5 the auth codes are the CLI's own, and rate_limit is excluded",
          sup._AUTH_ERROR_CODES == frozenset({
              "authentication_failed", "oauth_org_not_allowed",
              "account_on_hold"}), repr(sup._AUTH_ERROR_CODES))


def main() -> int:
    for fn in (s1_capture, s2_incident, s3_fallback_not_override,
               s4_recording_only, s5_controls):
        try:
            fn()
        except Exception:                                  # noqa: BLE001
            import traceback
            traceback.print_exc()
            FAILS.append(f"{fn.__name__} raised")
    for f in FAILS:
        print("   ·", f)
    if FAILS:
        print(f"\n{len(FAILS)} of {CHECKS} checks FAILED")
        return 1
    print(f"\nALL {CHECKS} CHECKS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
