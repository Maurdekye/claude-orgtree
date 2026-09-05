"""The OpenRouter lane classifies a failed turn by its TYPED HTTP status
(2026-09-05), and a gateway refusal ending in a clean, empty result is a
failure rather than a completed turn.

    python backend/tests/test_openrouter_typed_status.py   (no pytest)

WHAT WAS MEASURED, AND WHAT THIS RIG REPRODUCES
-----------------------------------------------
· A CLI transcript on this machine (Claude Code 2.1.258, entrypoint sdk-cli,
  an orgtree agent on `x-ai/grok-4.6` through openrouter.ai, 2026-09-03
  12:22:56Z) ends on a `<synthetic>` assistant record flagged
  `isApiErrorMessage`, `error: "unknown"`, `apiErrorStatus: 402`, whose text
  is "API Error: 402 This request would exceed your available credits given
  your current in-flight requests. Retry after in-flight requests settle, or
  add credits." That sentence has no "limit" in it, and the CLI's typed code
  was `unknown`, not `billing_error`. The result event that ended the session
  is NOT in the transcript; test_limit_freeze documents the clean-result
  ending as the measured shape for a synthetic API error, and the probe
  (codex-delivery/evidence) showed orgtree booking that ending as a COMPLETED
  turn — no error row, no mail, no freeze.
· With a Claude login present, `accounts.resolve("or-…")` answers "primary,
  available", so an OpenRouter 429 was RE-DRIVEN "on the next account in
  line" — the next spawn of an OR tier takes the same gateway key — instead
  of freezing (probe `or_redrive_probe.py`). audit F1's activation check ran
  on a signed-out rig and could not see it.
· OpenRouter's wording for 401/403/5xx is NOT observed; those rows carry
  labelled PLACEHOLDER text. The checks ask what orgtree does with a status
  and a sentence, never what the gateway says.

    §1  the strict status reader (pure)
    §2  the OpenRouter lane, login present — freeze, never re-drive
    §3  exclusive class by number: 403/limit terminal · 503/limit retry · 429/no-limit freeze
    §4  401 → parked auth freeze with the OpenRouter remedy
    §5  the measured 402 shape → failure, balance probe, F2 ownership, cap, manual resume, reset on success
    §6  coherence: an error retried past does not classify; the latest one does
    §7  the Claude lane is byte-for-byte unchanged (controls)

Anti-vacuity: `tests/_mutate_or_typed.py` breaks the shipped code ten ways
and requires a NAMED check here to go red for each.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
import traceback
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ⚠ the harness sets ORGTREE_DATA/HOME and writes its CLI stand-in AT IMPORT,
# before `orgtree` is imported anywhere in this process.
import test_limit_freeze as H                                    # noqa: E402

from orgtree import accounts, openrouter, store, supervisor     # noqa: E402
from orgtree.ledger import USER                                  # noqa: E402

_LIVE_ROOT = os.path.normcase(os.path.abspath(
    os.path.join(os.path.expanduser("~"), "orgtree")))
assert os.path.normcase(os.path.abspath(store.DATA_ROOT)) != _LIVE_ROOT, \
    f"store.DATA_ROOT resolved to the LIVE root: {store.DATA_ROOT}"
assert os.path.normcase(os.path.abspath(store.DATA_ROOT)).startswith(
    os.path.normcase(os.path.abspath(H._TMP))), store.DATA_ROOT
assert os.path.normcase(os.path.abspath(openrouter._dir())).startswith(
    os.path.normcase(os.path.abspath(H._TMP))), openrouter._dir()

PASS = 0
FAIL: list[tuple[str, str]] = []
VERBOSE = "-v" in sys.argv
ONLY = [a for a in sys.argv[1:] if not a.startswith("-")]

OR = "or-typed-fake"
OR_MODEL = "typed/fake"

#: the captured 402 (see the module docstring) — a TRANSIENT refusal
REAL_402 = ("API Error: 402 This request would exceed your available credits "
            "given your current in-flight requests. Retry after in-flight "
            "requests settle, or add credits.")
#: audit F1's fixture wording — carries "limit" + "exceeded"
OR_429 = ('API Error: 429 {"error":{"message":"Rate limit exceeded: '
          'free-models-per-day. Add 10 credits to unlock 1000 free model '
          'requests per day","code":429}}')
#: PLACEHOLDER wordings — deliberately built to carry the word "limit" so the
#: prose predicate admits them; whether OpenRouter ever says this is unknown
PH_403_LIMIT = 'API Error: 403 PLACEHOLDER forbidden: key usage limit policy'
PH_503_LIMIT = 'API Error: 503 PLACEHOLDER upstream usage limit reached, try later'
PH_429_NOLIMIT = 'API Error: 429 PLACEHOLDER too many requests'
PH_401 = 'API Error: 401 PLACEHOLDER no auth credentials found'


def check(label: str, fn) -> None:
    global PASS
    if ONLY and not any(o in label for o in ONLY):
        return
    try:
        fn()
    except Exception:                                            # noqa: BLE001
        FAIL.append((label, traceback.format_exc()))
        print(f"  FAIL     {label}")
        if VERBOSE:
            traceback.print_exc()
        return
    PASS += 1
    print(f"  ok {PASS:3d}  {label}")


def fixture(ok: bool, msg: str) -> None:
    if not ok:
        raise RuntimeError(f"fixture: {msg}")


# ══════════════════════════════════════════════════ the stand-in (steps)
#
# H's synthcli answers one fixed shape per mode. These checks need SEQUENCES
# — a synthetic error, then a real message, then a result — so this rig
# overwrites the SAME file H pointed ORGTREE_CLAUDE_CLI at (read at import)
# with a step-driven stand-in. `steps` is a list; each step is one stdout
# event, in order. Nothing here is a claim about the shipped CLI beyond the
# shapes named in the module docstring.

STEPS_JS = r"""
'use strict'
const fs = require('fs'), os = require('os'), path = require('path')
const argv = process.argv.slice(2)
if (argv.includes('--version')) { console.log('9.9.9 (stepscli)'); process.exit(0) }
function arg(n) { const i = argv.indexOf(n); return i >= 0 && i + 1 < argv.length ? argv[i + 1] : null }
let cfg = { steps: [] }
try { cfg = Object.assign(cfg, JSON.parse(fs.readFileSync(process.env.SYNTHCLI_CONFIG, 'utf8'))) } catch (e) {}
const sid = arg('--session-id') || arg('--resume') || 'no-session'
const home = process.env.USERPROFILE || process.env.HOME || os.homedir()
const projDir = path.join(home, '.claude', 'projects',
  process.cwd().replace(/[\\/:]+/g, '-').replace(/^-+/, ''))
fs.mkdirSync(projDir, { recursive: true })
const tpath = path.join(projDir, sid + '.jsonl')
function record(rec) {
  if (!rec.timestamp) rec.timestamp = new Date().toISOString()
  const fd = fs.openSync(tpath, 'a')
  fs.writeSync(fd, JSON.stringify(rec) + '\n'); fs.fsyncSync(fd); fs.closeSync(fd)
}
function say(o) { fs.writeSync(1, JSON.stringify(o) + '\n') }
function served(text) {
  try { fs.appendFileSync(process.env.SYNTHCLI_COUNT, JSON.stringify(text) + '\n') } catch (e) {}
}
say({ type: 'system', subtype: 'init', model: arg('--model') || 'fake', permissionMode: 'acceptEdits',
      cwd: process.cwd(), tools: [], mcp_servers: [] })
function serve(text) {
  served(text)
  record({ type: 'user', message: { role: 'user', content: text } })
  for (const s of cfg.steps) {
    if (s.kind === 'assistant') {
      const msg = { role: 'assistant', model: s.model || 'fake',
                    content: [{ type: 'text', text: s.text || 'ack.' }], usage: { input_tokens: 1000, output_tokens: 10 } }
      say({ type: 'assistant', message: msg }); record({ type: 'assistant', message: msg })
    } else if (s.kind === 'synthetic') {
      const msg = { role: 'assistant', model: '<synthetic>', content: [{ type: 'text', text: s.text }],
                    usage: { input_tokens: 0, output_tokens: 0 }, stop_reason: 'stop_sequence' }
      const ev = { type: 'assistant', message: msg, isApiErrorMessage: true, error: s.code || 'unknown' }
      if (s.status !== undefined) ev.apiErrorStatus = s.status
      say(ev); record(ev)
    } else if (s.kind === 'retry') {
      say({ type: 'system', subtype: 'api_retry', error: s.code || 'server_error' })
    } else if (s.kind === 'result') {
      const res = { type: 'result', subtype: 'success', is_error: !!s.is_error, result: s.text || '',
                    usage: { input_tokens: 1000, output_tokens: 10 }, total_cost_usd: 0.0001 }
      if (s.status !== undefined) res.api_error_status = s.status
      say(res)
      if (s.exit) process.exit(s.exit)
    }
  }
}
let buf = ''
process.stdin.setEncoding('utf8')
process.stdin.on('data', (d) => {
  buf += d
  let i
  while ((i = buf.indexOf('\n')) >= 0) {
    const line = buf.slice(0, i).trim(); buf = buf.slice(i + 1)
    if (!line) continue
    let ev; try { ev = JSON.parse(line) } catch (e) { continue }
    if (ev.type === 'control_request') { say({ type: 'control_response', response: { subtype: 'success' } }); continue }
    if (ev.type !== 'user') continue
    const c = ev.message && ev.message.content
    serve(typeof c === 'string' ? c : (c || []).map((b) => b.text || '').join(''))
  }
})
process.stdin.on('end', () => process.exit(0))
"""
with open(H._CLI, "w", encoding="utf-8") as _f:
    _f.write(STEPS_JS)


def steps(*items: dict[str, Any]) -> None:
    with open(H._CFG, "w", encoding="utf-8") as f:
        json.dump({"steps": list(items)}, f)
    open(H._COUNT, "w", encoding="utf-8").close()


def synthetic(text: str, status: int | None, code: str = "unknown") -> dict[str, Any]:
    d: dict[str, Any] = {"kind": "synthetic", "text": text, "code": code}
    if status is not None:
        d["status"] = status
    return d


def assistant(text: str = "ack.") -> dict[str, Any]:
    return {"kind": "assistant", "text": text}


def err_result(text: str, status: Any = None, exit_code: int = 0) -> dict[str, Any]:
    d: dict[str, Any] = {"kind": "result", "is_error": True, "text": text}
    if status is not None:
        d["status"] = status
    if exit_code:
        d["exit"] = exit_code
    return d


def clean_result(text: str = "") -> dict[str, Any]:
    return {"kind": "result", "is_error": False, "text": text}


NOTIFIED: list[tuple[str, str]] = []
supervisor.notify = lambda slug, nid, event: NOTIFIED.append((nid, event))   # type: ignore[assignment]
supervisor.stream = lambda slug, nid, payload: None                          # type: ignore[assignment]


def team(tier: str = OR) -> tuple[str, str, str]:
    """boss (haiku, top level) + one report on `tier`; an OR tier is
    registered on the org the way `openrouter.favorites` does it."""
    org = store.create_org(f"zz ortyped {time.time_ns()}")
    tl = {"bash": False, "web": False, "edit": False, "subagents": False,
          "mcp": []}
    boss = org.hire(USER, None, "haiku", 60, "boss", add_dirs=[], tools=tl,
                    org_visibility="team", charter="b")["node"]
    nid = org.hire(boss, boss, "haiku", 5, "rep", add_dirs=[], tools=tl,
                   org_visibility="team", charter="r")["node"]
    n = org.node(nid)
    n["model"] = tier
    if openrouter.is_tier(tier):
        org.d.setdefault("tiers", {})[tier] = 1
        org.d.setdefault("models", {})[tier] = OR_MODEL
    org.d["auto_resume"] = True
    store.save_org(org)
    return org.d["slug"], boss, nid


def run(slug: str, nid: str, text: str = "hello") -> str | None:
    """One real turn. `_run_one_turn` RECORDS a failure (last_error + the
    durable row) and returns; it does not re-raise. The in-memory banner is
    the return value here, so a check can say "failed" or "completed" from
    the same fact the desk shows."""
    try:
        supervisor._run_one_turn(slug, nid, text)
    except Exception as e:                                       # noqa: BLE001
        if VERBOSE:
            traceback.print_exc()
        return str(e)
    return supervisor.state(slug, nid).get("last_error") or None


def settle(slug: str, nid: str, timeout: float = 20.0) -> None:
    """Wait for a threaded (resumed) turn to leave the node."""
    t0 = time.time()
    time.sleep(0.3)
    while time.time() - t0 < timeout:
        st = supervisor.state(slug, nid)
        if st.get("proc") is None and not st.get("busy") \
                and not st.get("responding"):
            return
        time.sleep(0.1)
    raise RuntimeError("the resumed turn did not settle")


def node(slug: str, nid: str) -> dict[str, Any]:
    return store.load_org(slug).nodes[nid]


def rows(slug: str, nid: str) -> list[str]:
    return [r.get("text", "") for r in
            (store.load_org(slug).d.get("turn_error_log") or {}).get(nid, [])]


def sys_mail(slug: str, nid: str, needle: str = "") -> list[str]:
    log = store.load_org(slug).d.get("mail_log", {}).get(nid, [])
    return [m.get("body", "") for m in log if m.get("from") == "@system"
            and (not needle or needle in m.get("body", ""))]


# ══════════════════════════════════════════════════════════════════════ §1

def sec_reader() -> None:
    print("\n§1  the strict status reader")
    S = supervisor._strict_http_status
    T = supervisor._typed_api_status

    check("reader · an int error status is read",
          lambda: [None for _ in [0] if S(402) == 402 and S(599) == 599 and S(400) == 400] or (_ for _ in ()).throw(AssertionError(S(402))))

    def _rejects_lookalikes():
        for v in (True, False, "401", "402", 401.0, 4010, 399, 600, None, [401], {"code": 401}):
            assert S(v) is None, f"{v!r} was read as a status: {S(v)}"
    check("reader · bool, digit string, float, out-of-range and containers are NOT statuses",
          _rejects_lookalikes)

    def _result_first():
        assert T({"is_error": True, "api_error_status": 401}, {"status": 402}) == 401
    check("reader · the is_error result's number outranks the stream's", _result_first)

    def _clean_result_ignores_its_number():
        assert T({"is_error": False, "api_error_status": 401}, {}) is None, (
            "a status on a result NOT flagged is_error was believed")
        assert T({"api_error_status": 401}, {}) is None
    check("reader · a status on a clean result is not evidence", _clean_result_ignores_its_number)

    def _stream_fallback():
        assert T({"is_error": True}, {"status": 402}) == 402
        assert T({}, {"status": 402}) == 402
        assert T({"is_error": True, "api_error_status": "401"}, {"status": 402}) == 402, (
            "a digit STRING on the result displaced the stream's real number")
        assert T({}, {}) is None
    check("reader · the stream's synthetic status is the fallback, never a coercion", _stream_fallback)

    def _latest_wins_and_clears():
        into: dict[str, Any] = {}
        supervisor._note_synthetic_status(into, {"apiErrorStatus": 401}, "first")
        supervisor._note_synthetic_status(into, {"apiErrorStatus": 402}, "second")
        assert (into["status"], into["status_text"]) == (402, "second"), into
        supervisor._note_synthetic_status(into, {"apiErrorStatus": "500"}, "junk")
        assert into["status"] == 402, "a non-int status moved the slot"
        supervisor._clear_synthetic_status(into)
        assert "status" not in into and "status_text" not in into
    check("reader · the synthetic slot is LATEST-wins, moves as a pair, and clears", _latest_wins_and_clears)


# ══════════════════════════════════════════════════════════════════════ §2

def sec_no_redrive() -> None:
    print("\n§2  the OpenRouter lane with a login present — freeze, never re-drive")
    undo = H._stub_login()
    try:
        fixture(accounts.resolve(OR).get("available") is True, (
            "the rig's resolver does not offer capacity for an OR tier — the "
            "re-drive this section pins would be unreachable and every check "
            "below vacuous"))
        _sec_no_redrive_body()
    finally:
        undo()


def _sec_no_redrive_body() -> None:
    slug, boss, nid = team()
    steps(assistant(OR_429), err_result(OR_429, 429))
    run(slug, nid)
    fz = dict(node(slug, nid).get("frozen") or {})
    st = supervisor.state(slug, nid)

    def _typed_429_freezes():
        assert fz.get("limit") is True, f"not frozen as a limit: {fz} · rows {rows(slug, nid)}"
        assert fz.get("provider") == "openrouter", fz
        assert fz.get("account", "").startswith("openrouter-key:"), fz
        assert "pool" not in fz, f"the OR freeze asked the account resolver: pool={fz.get('pool')!r}"
    check("redrive · a typed OpenRouter 429 FREEZES with the login present", _typed_429_freezes)

    def _no_switch():
        assert not int(st.get("account_switches") or 0), (
            f"account_switches={st.get('account_switches')} — the OR turn was "
            "re-driven on 'the next account in line', which for an OR tier is "
            "the same gateway key and the same wall")
        assert not [r for r in rows(slug, nid) if "re-driven" in r or "account switched" in r], rows(slug, nid)
    check("redrive · …and no account switch was attempted or recorded", _no_switch)

    slug2, boss2, nid2 = team()
    steps(assistant(OR_429), err_result(OR_429))          # NO status number
    run(slug2, nid2)
    fz2 = dict(node(slug2, nid2).get("frozen") or {})

    def _prose_only_429_too():
        assert fz2.get("limit") is True, f"prose-only OR 429 did not freeze: {fz2}"
        assert not int(supervisor.state(slug2, nid2).get("account_switches") or 0), (
            "a PROSE-ONLY rate limit still went through the resolver — the "
            "bypass must key on the spawn identity, not on the typed status")
    check("redrive · a prose-only OpenRouter rate limit takes the same door", _prose_only_429_too)


# ══════════════════════════════════════════════════════════════════════ §3

def sec_exclusive() -> None:
    print("\n§3  the number chooses the class exclusively")
    undo = H._stub_login()
    try:
        _sec_exclusive_body()
    finally:
        undo()


def _sec_exclusive_body() -> None:
    fixture(supervisor._looks_like_usage_limit(PH_403_LIMIT)
            and supervisor._looks_like_usage_limit(PH_503_LIMIT)
            and not supervisor._looks_like_usage_limit(PH_429_NOLIMIT), (
                "the placeholder wordings do not exercise the prose predicate "
                "the way this section needs"))

    slug, boss, nid = team()
    steps(assistant(PH_403_LIMIT), err_result(PH_403_LIMIT, 403))
    run(slug, nid)

    def _403_terminal():
        n = node(slug, nid)
        assert not n.get("frozen"), (
            f"a typed 403 whose sentence says 'limit' was FROZEN: {n.get('frozen')}")
        assert int(n.get("hard_fail_run") or 0) == 1, n.get("hard_fail_run")
        told = sys_mail(slug, boss, "REPORT STALLED")
        assert told and "API status 403" in told[0], told
    check("exclusive · typed 403 with limit wording stays TERMINAL, status in the door", _403_terminal)

    slug, boss, nid = team()
    steps(assistant(PH_503_LIMIT), err_result(PH_503_LIMIT, 503))
    run(slug, nid)

    def _503_retry():
        fz = dict(node(slug, nid).get("frozen") or {})
        assert fz.get("connection") is True and not fz.get("limit"), (
            f"a typed 503 whose sentence says 'limit' was not routed to the "
            f"bounded retry: {fz}")
        assert "answered 503" in str(fz.get("until")), fz.get("until")
        assert int(node(slug, nid).get("net_fail_run") or 0) == 1
        assert not sys_mail(slug, boss), "a scheduled retry mailed the manager"
    check("exclusive · typed 503 with limit wording is a BOUNDED RETRY, not a limit", _503_retry)

    slug, boss, nid = team()
    steps(assistant(PH_429_NOLIMIT), err_result(PH_429_NOLIMIT, 429))
    run(slug, nid)

    def _429_no_wording():
        fz = dict(node(slug, nid).get("frozen") or {})
        assert fz.get("limit") is True and "cause" not in fz, (
            f"a typed 429 with no limit wording did not freeze as a limit: {fz}")
    check("exclusive · typed 429 WITHOUT limit wording freezes as a limit", _429_no_wording)

    slug, boss, nid = team()
    steps(assistant(PH_403_LIMIT), err_result(PH_403_LIMIT))    # NO number
    run(slug, nid)

    def _prose_when_untyped():
        fz = dict(node(slug, nid).get("frozen") or {})
        assert fz.get("limit") is True, (
            "with NO typed status the prose predicate must still govern — "
            f"the fallback was lost: {fz}")
    check("exclusive · with no number at all, prose still decides (control)", _prose_when_untyped)


# ══════════════════════════════════════════════════════════════════════ §4

def sec_auth() -> None:
    print("\n§4  401 → parked auth freeze with the OpenRouter remedy")
    undo = H._stub_login()
    try:
        _sec_auth_body()
    finally:
        undo()


def _sec_auth_body() -> None:
    slug, boss, nid = team()
    openrouter._key_cache = (time.time(), {"key_set": True, "connected": True})
    steps(assistant(PH_401), err_result(PH_401, 401))
    run(slug, nid)
    fz = dict(node(slug, nid).get("frozen") or {})

    def _parked_auth():
        assert fz.get("cause") == "auth", f"a typed 401 with no limit wording did not park as auth: {fz} · {rows(slug, nid)}"
        assert fz.get("until_ts") is None and fz.get("reset_src") == "auth", fz
        assert "OpenRouter key" in str(fz.get("until")) and "Providers" in str(fz.get("until")), fz.get("until")
    check("auth · typed 401 parks with cause=auth and names the OpenRouter door", _parked_auth)

    def _resumable_not_timed():
        o = store.load_org(slug)
        assert supervisor._resumable(o.node(nid)) is not None, "▶ would refuse the auth freeze"
        assert nid not in supervisor.auto_resume_ready(o, time.time() + 86400), "the timer would re-present a rejected key"
    check("auth · ▶ resumes it, the timer never does", _resumable_not_timed)

    def _panel_forgets():
        assert openrouter.cached_key_status() is None, (
            "the panel's cached 'connected' verdict survived a 401 on the lane")
    check("auth · the cached /api/v1/key verdict is dropped", _panel_forgets)

    def _told():
        told = sys_mail(slug, boss, "REJECTED")
        assert len(told) == 1, [t[:80] for t in sys_mail(slug, boss)]
    check("auth · the manager is told once (parked announce)", _told)


# ══════════════════════════════════════════════════════════════════════ §5

def sec_balance() -> None:
    print("\n§5  the measured 402 shape: failure, balance probe, F2 ownership, cap, resume")
    undo = H._stub_login()
    try:
        _sec_balance_body()
    finally:
        undo()


def _sec_balance_body() -> None:
    slug, boss, nid = team()
    steps(synthetic(REAL_402, 402), clean_result(""))
    raised = run(slug, nid)
    n1 = node(slug, nid)
    fz1 = dict(n1.get("frozen") or {})

    def _not_completed():
        assert raised, "the turn was booked as COMPLETED (no last_error)"
        assert "402" in raised, raised
        assert supervisor.state(slug, nid).get("turns_run", 0) == 0, "turns_run advanced"
        assert any("402" in r for r in rows(slug, nid)), rows(slug, nid)
    check("balance · the captured 402 ending on a clean empty result is a FAILURE", _not_completed)

    def _balance_freeze():
        assert fz1.get("cause") == "balance" and fz1.get("limit") is True, fz1
        assert fz1.get("provider") == "openrouter" and fz1.get("resource_pool") == OR, fz1
        assert fz1.get("schedule_kind") == "probe" and fz1.get("reset_src") == "probe", fz1
        assert fz1.get("until_ts") and fz1["until_ts"] - time.time() <= supervisor.PROBE_FLOOR + 1, fz1
        assert "check balance or in-flight requests" in str(fz1.get("until")), fz1.get("until")
        assert int(n1.get("balance_probe_run") or 0) == 1, n1.get("balance_probe_run")
        assert "on_fallback" not in fz1 and "pool" not in fz1, fz1
    check("balance · …frozen cause=balance on the probe floor, run 1, no window, no pool", _balance_freeze)

    def _quiet_and_manual():
        assert not sys_mail(slug, boss), (
            "a bounded probe mailed the manager as a wall (REPORT LIMITED)")
        o = store.load_org(slug)
        assert supervisor._resumable(o.node(nid)) is not None, "▶ would refuse a balance freeze"
        assert nid not in supervisor.auto_resume_ready(o, time.time()), "ready before its own probe time"
        assert nid in supervisor.auto_resume_ready(o, float(fz1["until_ts"]) + 61), "not ready after it"
    check("balance · quiet while probing; ▶ resumes; the timer waits for the horizon", _quiet_and_manual)

    def _f2_key():
        key = supervisor._limit_probe_key(node(slug, nid), fz1)
        assert key == ("openrouter", fz1["account"], OR), key
    check("balance · the probe is owned per (openrouter, key namespace, tier) — F2's key", _f2_key)

    # the timer's actual dispatch: consent → claim → resume, through the
    # probe-claim path (`limit` + `schedule_kind == "probe"`), three more
    # times against the same refusal — then the cap
    for i in range(2, supervisor.NET_RETRY_MAX + 1):
        fz_now = dict(node(slug, nid).get("frozen") or {})
        when = float(fz_now["until_ts"]) + 61
        with supervisor._limit_probe_lock:
            supervisor._limit_probes.clear()
            supervisor._limit_probe_last.clear()
        supervisor._auto_resume_org(slug, when)
        settle(slug, nid)
        fz_after = dict(node(slug, nid).get("frozen") or {})
        run_after = int(node(slug, nid).get("balance_probe_run") or 0)
        if i < supervisor.NET_RETRY_MAX:
            def _probe_i(i=i, fz_after=fz_after, run_after=run_after):
                assert run_after == i, (f"probe {i}: run={run_after}, fz={fz_after}")
                assert fz_after.get("cause") == "balance" and fz_after.get("until_ts"), fz_after
                assert f"({i}/{supervisor.NET_RETRY_MAX})" in str(fz_after.get("until")), fz_after.get("until")
            check(f"balance · timer probe {i} re-froze on the floor (dispatch through the claim path)", _probe_i)
    fz_cap = dict(node(slug, nid).get("frozen") or {})
    n_cap = node(slug, nid)

    def _capped():
        assert int(n_cap.get("balance_probe_run") or 0) == supervisor.NET_RETRY_MAX, n_cap.get("balance_probe_run")
        assert fz_cap.get("cause") == "balance" and fz_cap.get("until_ts") is None, fz_cap
        assert fz_cap.get("reset_src") == "capped", fz_cap
        assert "resume manually" in str(fz_cap.get("until")), fz_cap.get("until")
        o = store.load_org(slug)
        assert nid not in supervisor.auto_resume_ready(o, time.time() + 86400), "the timer still wakes a capped balance freeze"
        assert supervisor._resumable(o.node(nid)) is not None, "▶ refuses the capped freeze"
    check("balance · the cap parks it: no horizon, timer off, ▶ still on", _capped)

    def _cap_announced():
        told = sys_mail(slug, boss, "BALANCE")
        assert len(told) == 1, [t[:100] for t in sys_mail(slug, boss)]
        assert "NOT PROOF THE BALANCE IS EXHAUSTED" in told[0], told[0][:300]
    check("balance · the cap is announced ONCE, and says a 402 is not proof of exhausted funds", _cap_announced)

    def _manual_resume_then_success_resets():
        steps(assistant("served again"), clean_result("served again"))
        got = supervisor.resume_frozen(slug, only={nid})
        assert nid in got, got
        settle(slug, nid)
        n = node(slug, nid)
        assert not n.get("frozen"), n.get("frozen")
        assert "balance_probe_run" not in n, (
            "a SERVED turn did not reset the balance run: the next refusal "
            "would park at once instead of probing")
    check("balance · ▶ after a top-up: the served turn clears the run (reset only on success)", _manual_resume_then_success_resets)

    slug3, boss3, nid3 = team()
    steps(synthetic(REAL_402, 402), clean_result(""))
    run(slug3, nid3)
    steps(synthetic(REAL_402, 402), err_result(REAL_402, 402))
    supervisor.resume_frozen(slug3, only={nid3})
    settle(slug3, nid3)

    def _status_alone_never_resets():
        n = node(slug3, nid3)
        assert int(n.get("balance_probe_run") or 0) == 2, (
            f"a second refusal did not advance the run: {n.get('balance_probe_run')}")
    check("balance · a further refusal (is_error shape) advances the run; status alone never resets it", _status_alone_never_resets)


# ══════════════════════════════════════════════════════════════════════ §6

def sec_coherence() -> None:
    print("\n§6  coherence — a retried-past error does not classify; the latest one does")
    undo = H._stub_login()
    try:
        _sec_coherence_body()
    finally:
        undo()


def _sec_coherence_body() -> None:
    slug, boss, nid = team()
    steps(synthetic(REAL_402, 402), assistant("recovered, here is the answer"),
          clean_result("recovered, here is the answer"))
    raised = run(slug, nid)

    def _retried_past():
        assert raised is None, f"a turn that RECOVERED after a 402 was failed: {raised}"
        n = node(slug, nid)
        assert not n.get("frozen") and not rows(slug, nid), (n.get("frozen"), rows(slug, nid))
        assert supervisor.state(slug, nid).get("turns_run") == 1, "the recovered turn was not booked"
    check("coherence · synthetic 402 → real output → nonempty result is a COMPLETED turn", _retried_past)

    # ⚠ THE CHECK ABOVE CANNOT SEE THE CLEARING. Its result text is NONEMPTY,
    # and the clean-empty-result adoption never fires on a nonempty result —
    # so the turn stays completed whether or not the retried-past status was
    # retired. Removing `_clear_synthetic_status` left it green (mutation
    # round, 2026-09-05). THIS one is the same sequence ending on the EMPTY
    # clean result the adoption actually reads: the only thing standing
    # between it and a balance freeze is the clearing.
    slug, boss, nid = team()
    steps(synthetic(REAL_402, 402), assistant("recovered, and said nothing more"),
          clean_result(""))
    raised = run(slug, nid)

    def _retried_past_empty():
        assert raised is None, (
            f"a turn that produced a REAL assistant message after a 402 and "
            f"then ended on an empty clean result was failed on the "
            f"retried-past status: {raised}")
        n = node(slug, nid)
        assert not n.get("frozen"), n.get("frozen")
        assert not rows(slug, nid), rows(slug, nid)
        assert supervisor.state(slug, nid).get("turns_run") == 1, "the recovered turn was not booked"
    check("coherence · synthetic 402 → real output → EMPTY result is a COMPLETED turn (the clearing)", _retried_past_empty)

    slug, boss, nid = team()
    steps(assistant("partial work"), synthetic(REAL_402, 402), clean_result(""))
    raised = run(slug, nid)

    def _later_error():
        assert raised and "402" in raised, (
            f"real output followed by a 402 and an empty result was booked completed: {raised!r}")
        assert supervisor.state(slug, nid).get("turns_run", 0) == 0
        fz = dict(node(slug, nid).get("frozen") or {})
        assert fz.get("cause") == "balance", fz
    check("coherence · real output → LATER synthetic 402 → empty result classifies by the later error", _later_error)

    slug, boss, nid = team()
    steps(synthetic(PH_401, 401, "authentication_failed"), synthetic(REAL_402, 402),
          clean_result(""))
    run(slug, nid)

    def _consecutive_401_402():
        fz = dict(node(slug, nid).get("frozen") or {})
        assert fz.get("cause") == "balance", (
            f"consecutive 401 → 402 parked as AUTH on the stale first status: {fz}")
        assert fz.get("until_ts"), "…and it was parked without a probe horizon"
    check("coherence · consecutive 401 → 402 is the 402 (latest), not a stale auth park", _consecutive_401_402)

    slug, boss, nid = team()
    steps(synthetic(REAL_402, None), clean_result(""))
    raised = run(slug, nid)

    def _no_status_unchanged():
        assert raised is None and not node(slug, nid).get("frozen")             and not rows(slug, nid)             and supervisor.state(slug, nid).get("turns_run") == 1, (
            "a synthetic record with NO status changed behaviour — the "
            "adoption is gated on a number the model cannot emit")
    check("coherence · a synthetic error with no status is exactly yesterday's behaviour (control)", _no_status_unchanged)


# ══════════════════════════════════════════════════════════════════════ §7

def sec_claude_unchanged() -> None:
    print("\n§7  the Claude lane is unchanged (controls)")
    undo = H._stub_login()
    try:
        _sec_claude_body()
    finally:
        undo()


def _sec_claude_body() -> None:
    slug, boss, nid = team("haiku")
    steps(synthetic(REAL_402, 402), clean_result(""))
    raised = run(slug, nid)

    def _claude_synthetic_402_untouched():
        assert raised is None and not node(slug, nid).get("frozen")             and not rows(slug, nid)             and supervisor.state(slug, nid).get("turns_run") == 1, (
            "the OR-only adoption reached a CLAUDE turn")
    check("claude · the same synthetic 402 + clean result on a haiku node is untouched (scope)", _claude_synthetic_402_untouched)

    slug, boss, nid = team("haiku")
    steps(assistant(PH_403_LIMIT), err_result(PH_403_LIMIT, 403))
    run(slug, nid)

    def _claude_prose_still_governs():
        fz = dict(node(slug, nid).get("frozen") or {})
        assert fz.get("limit") is True, (
            f"a CLAUDE turn with a 403 and limit wording stopped freezing — "
            f"the typed class leaked out of the OR lane: {fz}")
    check("claude · a typed 403 with limit wording still freezes on the Claude lane (prose governs)", _claude_prose_still_governs)

    slug, boss, nid = team("haiku")
    steps(assistant(H.REAL), err_result(H.REAL, 401))
    run(slug, nid)

    def _claude_auth_park_unchanged():
        fz = dict(node(slug, nid).get("frozen") or {})
        assert fz.get("cause") == "auth" and fz.get("until_ts") is None, fz
        assert "credential rejected — replace it, then resume" == fz.get("until"), fz.get("until")
    check("claude · D-156's 401-with-limit-wording park keeps its exact label", _claude_auth_park_unchanged)


def main() -> None:
    openrouter.set_key("or-test-key-000000")
    try:
        sec_reader()
        sec_no_redrive()
        sec_exclusive()
        sec_auth()
        sec_balance()
        sec_coherence()
        sec_claude_unchanged()
    finally:
        openrouter.set_key("")
    for label, tb in FAIL:
        print(f"\n--- {label}\n{tb}")
    if FAIL:
        print(f"\n{len(FAIL)} of {PASS + len(FAIL)} checks FAILED")
    else:
        print(f"\nALL {PASS} CHECKS PASS")
    try:
        shutil.rmtree(H._TMP, ignore_errors=True)
    except OSError:
        pass
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
