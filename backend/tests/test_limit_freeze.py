"""Usage-limit FREEZE — does the machinery fire on the shape the real CLI uses?

User report 2026-08-05:

    "frozen state appears not to function correctly when session limits are
     hit; the session limit reached message is correctly identified and
     uniquely rendered as a small card, but the node it occurs on does not
     freeze, and no resume button appears anywhere."

Two halves of that report point at different code, and the contradiction is the
whole lead: the card the reader sees is drawn by `read_chat` from a TRANSCRIPT
record (`isApiErrorMessage` / `model:"<synthetic>"` → a `role:"system"` "⚠ …"
row, supervisor.py:3737), while the freeze is decided in the turn loop from
`err_blob` — which is stderr when the process exits non-zero, and the result
event's `result` ONLY when that event carries `is_error` (supervisor.py:1777).
Those are different sources. A limit that arrives as a synthetic assistant
message and a clean result event is therefore fully rendered and completely
invisible to the freeze path.

The wording is not hypothetical. Harvested from this machine's own CLI
transcripts (~/.claude/projects/**.jsonl):

    {"type":"assistant","isApiErrorMessage":true,
     "message":{"model":"<synthetic>",
                "content":"You've hit your limit · resets 12:40am (Asia/Jerusalem)"}}

Corroborating evidence for the same conclusion: across every org doc on disk —
live AND deleted — there is not one `frozen` record and not one
`turn_error_log` row. The user has seen the card; orgtree has never once
written the failure down. A limit that reached `err_blob` would have left both.

    §1  detection — the predicate against the phrasings actually observed
    §2  the shapes, end to end, through the real turn loop with a CLI stand-in
        that reports the limit the way the CLI does
    §3  what the reader is left with: the card, the button, the record
    §4  the fix, attacked — false positives and the way out
    §5  WHICH frozen node the auto-resume timer wakes, and when (peer report
        2026-08-10: an org-wide gate starved every short freeze behind the
        longest one). Needs no CLI, so it runs first.

Hermetic-ish: throwaway ORGTREE_DATA + HOME, no port, no Docker, no real CLI,
no network. §2 spawns `node` (the stand-in) — skipped with a note if absent.

    python backend/tests/test_limit_freeze.py [-v]
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import traceback

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

_TMP = tempfile.mkdtemp(prefix="orgtree-limitfreeze-")
_HOME = os.path.join(_TMP, "home")
_CLI = os.path.join(_TMP, "synthcli.js")
_CFG = os.path.join(_TMP, "synthcli.json")
_COUNT = os.path.join(_TMP, "served.log")
os.makedirs(_HOME, exist_ok=True)
os.environ["ORGTREE_DATA"] = os.path.join(_TMP, "data")

# ⚠ a throwaway ORGTREE_DATA does NOT isolate the MAIL HUB: net._default_address
# falls back to net.DEFAULT_HUB_ADDRESS — the operator's real hub — when this
# root has no defaults.json, and any rig that starts the net daemon then
# registers its fixture orgs there permanently. Measured twice (user report
# 2026-08-06; ~45 fixture orgs again on 2026-08-10). The discard port refuses
# instantly, so registration fails harmlessly into the backoff.
# Guarded over this whole directory by test_external_mail §1.
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w",
          encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

os.environ["USERPROFILE"] = _HOME
os.environ["HOME"] = _HOME
os.environ["ORGTREE_STEER_HOOK"] = "0"
os.environ["ORGTREE_CLAUDE_CLI"] = _CLI      # read at import time
os.environ["SYNTHCLI_CONFIG"] = _CFG
os.environ["SYNTHCLI_COUNT"] = _COUNT

from orgtree import store, supervisor                            # noqa: E402
from orgtree.ledger import USER                                  # noqa: E402

PASS = 0
FAIL: list[tuple[str, str]] = []
GAPS: list[tuple[str, str, str]] = []
NOTES: list[str] = []
VERBOSE = "-v" in sys.argv

#: verbatim from a real transcript on this machine (see the module docstring)
REAL = "You've hit your limit · resets 12:40am (Asia/Jerusalem)"


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


def fixture(ok, msg) -> None:
    """A PRECONDITION inside a gap body — raised as a RuntimeError so `gap`
    below re-reports it as a broken check instead of swallowing it as the
    finding.

    ⚠ Learned the expensive way (2026-08-06, test_batched_asks). A gap
    body's whole contract is "this assert fails", so a fixture assert and the
    assert that measures the defect are indistinguishable: gap() catches the
    first AssertionError it meets and files it as the finding. A credit
    request for 8 against a grant of 20 took the at-or-below no-op branch, so
    no row ever existed — the gap fired on its own scaffolding while the
    defect it named was real but unexercised. Use fixture(...) for every setup
    precondition in a gap body; keep a bare `assert` for the property under
    test."""
    if not ok:
        raise RuntimeError(f"fixture: {msg}")


def gap(label, why, fn) -> None:
    """A property that SHOULD hold and currently does not — inverted so the
    suite stays green today and turns RED the day it is fixed."""
    global PASS
    try:
        fn()
    except AssertionError as e:
        GAPS.append((label, why, str(e).split("\n")[0][:300]))
        print(f"  ⚑ GAP    {label}")
        return
    except Exception:                                            # noqa: BLE001
        FAIL.append((label + " (gap check errored)", traceback.format_exc()))
        print(f"  FAIL     {label} — the gap check itself broke")
        return
    PASS += 1
    print(f"  ok {PASS:3d}  {label}  ← FIXED: promote this out of gap()")


def note(msg: str) -> None:
    NOTES.append(msg)
    print(f"       · {msg}")


# ══════════════════════════════════════════════════════════════ the CLI stand-in
#
# Deliberately NOT fakecli.js: that shim's `usageLimit` dial answers with an
# `is_error` result, i.e. the one shape the freeze path already handles. What
# has to be reproduced here is the shape the CLI actually uses — a synthetic
# assistant message, flagged as an API error, written to the transcript, with
# the process exiting cleanly and the result event reporting success.

SYNTH_JS = r"""
'use strict'
const fs = require('fs'), os = require('os'), path = require('path')
const argv = process.argv.slice(2)
if (argv.includes('--version')) { console.log('9.9.9 (synthcli)'); process.exit(0) }
function arg(n) { const i = argv.indexOf(n); return i >= 0 && i + 1 < argv.length ? argv[i + 1] : null }
let cfg = { mode: 'plain', limitText: 'limit', echoResult: false }
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
function say(o) { process.stdout.write(JSON.stringify(o) + '\n') }
function served(text) {
  try { fs.appendFileSync(process.env.SYNTHCLI_COUNT, JSON.stringify(text) + '\n') } catch (e) {}
}

say({ type: 'system', subtype: 'init', model: 'fake', permissionMode: 'acceptEdits',
      cwd: process.cwd(), tools: [], mcp_servers: [] })

function serve(text) {
  served(text)
  record({ type: 'user', message: { role: 'user', content: text } })
  if (cfg.mode === 'iserror') {
    // the COVERED shape: the CLI answers with an is_error result carrying the
    // limit text (this is what fakecli.js's usageLimit dial does)
    const msg = { role: 'assistant', model: 'fake',
                  content: [{ type: 'text', text: cfg.limitText }],
                  usage: { input_tokens: 1000 } }
    say({ type: 'assistant', message: msg })
    record({ type: 'assistant', message: msg })
    say({ type: 'result', subtype: 'success', is_error: true, result: cfg.limitText,
          usage: { input_tokens: 1000 }, total_cost_usd: 0.0001 })
    return
  }
  if (cfg.mode === 'synthetic') {
    // THE REAL SHAPE. The engine speaks, not the model: model "<synthetic>",
    // the record flagged isApiErrorMessage — and the turn then ENDS NORMALLY.
    const msg = { role: 'assistant', model: '<synthetic>',
                  content: [{ type: 'text', text: cfg.limitText }],
                  usage: { input_tokens: 0 } }
    say({ type: 'assistant', message: msg, isApiErrorMessage: true })
    record({ type: 'assistant', message: msg, isApiErrorMessage: true })
    say({ type: 'result', subtype: 'success', is_error: false,
          result: cfg.echoResult ? cfg.limitText : '',
          usage: { input_tokens: 0 }, total_cost_usd: 0 })
    return
  }
  if (cfg.mode === 'benign-synthetic') {
    // the OTHER synthetic record the CLI writes constantly — same two flags,
    // nothing to do with a limit. Must not fail the turn.
    const msg = { role: 'assistant', model: '<synthetic>',
                  content: 'No response requested.', usage: {} }
    say({ type: 'assistant', message: msg, isApiErrorMessage: false })
    record({ type: 'assistant', message: msg })
    say({ type: 'result', subtype: 'success', is_error: false, result: '',
          usage: {}, total_cost_usd: 0 })
    return
  }
  // plain: the agent's own answer, and the result event that carries it —
  // in stream-json the result's `result` IS the assistant's final text
  const reply = cfg.replyText || 'ack.'
  const msg = { role: 'assistant', model: 'fake',
                content: [{ type: 'text', text: reply }],
                usage: { input_tokens: 1000 } }
  say({ type: 'assistant', message: msg })
  record({ type: 'assistant', message: msg })
  say({ type: 'result', subtype: 'success', is_error: false, result: reply,
        usage: { input_tokens: 1000 }, total_cost_usd: 0.0001 })
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
    if (ev.type === 'control_request') {
      say({ type: 'control_response', response: { subtype: 'success' } }); continue
    }
    if (ev.type !== 'user') continue
    const c = ev.message && ev.message.content
    serve(typeof c === 'string' ? c : (c || []).map((b) => b.text || '').join(''))
  }
})
process.stdin.on('end', () => process.exit(0))
"""

with open(_CLI, "w", encoding="utf-8") as _f:
    _f.write(SYNTH_JS)


def set_mode(mode: str, echo_result: bool = False, limit_text: str = REAL,
             reply: str = "ack.") -> None:
    """Reprogram the stand-in for the next launch (it re-reads on every run)."""
    with open(_CFG, "w", encoding="utf-8") as f:
        json.dump({"mode": mode, "limitText": limit_text,
                   "echoResult": echo_result, "replyText": reply}, f)
    open(_COUNT, "w", encoding="utf-8").close()


def served() -> list[str]:
    try:
        return [json.loads(x) for x in
                open(_COUNT, encoding="utf-8").read().splitlines() if x.strip()]
    except OSError:
        return []


_n = [0]


def probe_org() -> tuple[str, str]:
    """A saved one-agent org in the throwaway data root."""
    _n[0] += 1
    org = store.create_org(f"zz limitfreeze {_n[0]}")
    r = org.hire(USER, None, "haiku", 20, "probe",
                 add_dirs=[], tools={"bash": False, "web": False, "edit": False,
                                     "subagents": False, "mcp": []},
                 org_visibility="team", charter="freeze probe")
    store.save_org(org)
    return org.d["slug"], r["node"]


def run_turn(slug: str, nid: str, text: str = "hello") -> None:
    """One real turn through the real loop. `_run_one_turn` raises on a failed
    turn exactly as the worker thread's caller sees it; a limit is a failure,
    so swallowing it here is the honest shape."""
    try:
        supervisor._run_one_turn(slug, nid, text)
    except Exception:                                            # noqa: BLE001
        if VERBOSE:
            traceback.print_exc()


def node(slug: str, nid: str) -> dict:
    return store.load_org(slug).nodes[nid]


def chat_rows(slug: str, nid: str) -> list[dict]:
    return supervisor.read_chat(store.load_org(slug), nid)["messages"]


# ══════════════════════════════════════════════════════════════════════════ §1

def sec_detect() -> None:
    print("\n§1  detection — the predicate vs the phrasings actually seen")

    def _yes(blob: str) -> None:
        assert supervisor._looks_like_usage_limit(blob), f"not detected: {blob!r}"

    check("detect · the verbatim wording harvested from this machine's "
          "transcripts", lambda: _yes(REAL))
    check("detect · the epoch form", lambda: _yes(
        "Claude AI usage limit reached|1753898400"))
    check("detect · session-limit phrasing", lambda: _yes(
        "You've hit your session limit — resets 1:40pm"))
    note("detection is NOT the defect: every wording seen, including the one "
         "in the user's report, matches _looks_like_usage_limit. The defect is "
         "WHERE the predicate is applied.")


# ══════════════════════════════════════════════════════════════════════════ §2

def sec_shapes() -> None:
    print("\n§2  the shapes, end to end, through the real turn loop")

    # ── control: the shape the freeze path was written against ──────────────
    slug_c, nid_c = probe_org()

    def _iserror_freezes():
        set_mode("iserror")
        run_turn(slug_c, nid_c)
        fz = node(slug_c, nid_c).get("frozen")
        assert fz, "the covered shape stopped freezing — the rig or the path broke"
        assert fz.get("limit") is True, f"not tagged as a usage-limit freeze: {fz}"
    check("control · an is_error result carrying the limit text DOES freeze "
          "(the rig works, and so does the covered path)", _iserror_freezes)

    # ── the real shape ──────────────────────────────────────────────────────
    slug_a, nid_a = probe_org()

    def _synthetic_freezes():
        set_mode("synthetic")
        run_turn(slug_a, nid_a)
        fz = node(slug_a, nid_a).get("frozen")
        assert fz, (
            "the CLI reported a usage limit as a <synthetic> assistant message "
            "flagged isApiErrorMessage and exited cleanly — the node did not "
            "freeze, so there is nothing for ▶ resume to find")
    # was a gap: err_blob only saw stderr (non-zero exit) or `result` under
    # is_error — the CLI's real limit report (assistant message, model
    # "<synthetic>", isApiErrorMessage, clean exit 0) matched neither. Fixed
    # 2026-08-05: the stream loop captures the synthetic text and the
    # err_blob path adopts it, driving the existing freeze branch.
    check("freeze · a limit reported as a <synthetic> assistant message "
          "freezes the node", _synthetic_freezes)

    slug_b, nid_b = probe_org()

    def _synthetic_with_text_in_result_freezes():
        set_mode("synthetic", echo_result=True)
        run_turn(slug_b, nid_b)
        fz = node(slug_b, nid_b).get("frozen")
        assert fz, (
            "the limit text was IN the result event and still did not freeze — "
            "err_blob only reads `result` when is_error is set")
    # was a gap: `result` was discarded unread unless is_error was set. Fixed
    # 2026-08-05, with a deliberate bound: the flagless result-text fallback
    # fires only on SHORT (<200 char) standalone texts, so a long genuine
    # answer that merely DISCUSSES limits cannot freeze its author.
    check("freeze · a limit named in the result event freezes even when "
          "is_error is not set", _synthetic_with_text_in_result_freezes)

    # ── what the turn is recorded as ────────────────────────────────────────
    def _no_silent_success():
        n = node(slug_a, nid_a)
        turns = n.get("turns") or []
        assert not turns or turns[-1].get("killed"), (
            "the limited turn was booked as a normal completed turn "
            f"({turns[-1] if turns else None}) — the agent answered nothing")
    # was a gap: the loop fell through to the success tail (last_error
    # cleared, turns_run++, _after_turn charging). The adopted err_blob now
    # raises before any of that runs.
    check("bookkeeping · a turn that produced only a limit notice is not "
          "booked as a completed turn", _no_silent_success)

    def _durable_record():
        rows = (store.load_org(slug_a).d.get("turn_error_log") or {}).get(nid_a) or []
        assert rows, (
            "no turn_error_log row — the limit left NO durable trace in the "
            "org doc (which is why no doc on disk has ever recorded one)")
    # was a gap: `_log_turn_error` only runs from the turn's `except`, which
    # the synthetic shape never reached (live install had ZERO rows while
    # the user had seen the card). The raise now routes through it.
    check("record · a limit leaves a durable row in the org doc",
          _durable_record)

    # ── the queue keeps draining into a live limit ──────────────────────────
    slug_q, nid_q = probe_org()

    def _queue_stops():
        set_mode("synthetic")
        st = supervisor.state(slug_q, nid_q)
        st["queue"].extend(["second", "third"])
        run_turn(slug_q, nid_q, "first")
        n = len(served())
        assert n == 1, (
            f"{n} messages were fed to a session that had just reported a "
            f"usage limit (queue drained at the result boundary)")
    # was a gap: the boundary feed's `limited` flag rode the same is_error
    # gate ("frozenq" through the other door — measured 3 real attempts
    # against a live limit). `limited` is now set from the synthetic capture
    # and the short-result fallback too.
    check("queue · a session that reports a limit is not fed the next "
          "queued message", _queue_stops)


# ══════════════════════════════════════════════════════════════════════════ §3

def sec_reader() -> None:
    print("\n§3  what the reader is left with")

    slug, nid = probe_org()
    set_mode("synthetic")
    run_turn(slug, nid)

    def _card_is_drawn():
        rows = chat_rows(slug, nid)
        sys_rows = [r for r in rows if r.get("role") == "system"
                    and "limit" in (r.get("text") or "")]
        assert sys_rows, f"no system row carrying the limit: {rows}"
        assert sys_rows[0]["text"].startswith("⚠ "), sys_rows[0]
    check("card · the limit IS rendered — a system '⚠ …' row, from the "
          "transcript's isApiErrorMessage record (the user's 'small card')",
          _card_is_drawn)

    def _not_in_agent_voice():
        rows = chat_rows(slug, nid)
        bad = [r for r in rows if r.get("role") == "assistant"
               and "hit your limit" in (r.get("text") or "")]
        assert not bad, f"the engine spoke in the agent's voice: {bad}"
    check("card · and never in the agent's own voice (№8 holds on the durable "
          "side)", _not_in_agent_voice)

    # ANTI-VACUITY. The gap below asserts "the tree payload carries a frozen
    # node" and expects it to fail — which it would also do if `_flat` simply
    # could not see nodes. So the same walk is first shown finding a freeze it
    # is supposed to find, on a node frozen through the covered path.
    slug_f, nid_f = probe_org()
    set_mode("iserror")
    run_turn(slug_f, nid_f)

    def _walk_sees_a_real_freeze():
        seen = [n["id"] for n in _flat(store.load_org(slug_f).tree())]
        assert seen, "the tree walk found no nodes at all"
        frozen = [n for n in _flat(store.load_org(slug_f).tree()) if n.get("frozen")]
        assert [n["id"] for n in frozen] == [nid_f], f"walked {seen}, frozen {frozen}"
    check("button · the same tree walk DOES surface a node frozen through the "
          "covered path (so the gap below is a real absence, not a blind walk)",
          _walk_sees_a_real_freeze)

    def _button_precondition():
        tree = store.load_org(slug).tree()
        frozen = [n for n in _flat(tree) if n.get("frozen")]
        assert frozen, (
            "no node in the tree payload carries `frozen`, so App.tsx's "
            "▶ resume block returns null — exactly what the user reported")
    # was a gap: no freeze ⇒ no `frozen` in the payload ⇒ App.tsx's ▶ resume
    # block returned null. Listed separately because it is what the user
    # sees; the freeze fix feeds it.
    check("button · the tree payload gives ▶ resume something to find",
          _button_precondition)

    def _card_and_state_agree():
        rows = chat_rows(slug, nid)
        has_card = any(r.get("role") == "system" and "limit" in (r.get("text") or "")
                       for r in rows)
        assert not has_card or node(slug, nid).get("frozen"), (
            "the conversation shows a usage-limit card while the node's state "
            "says nothing happened — the reader is told two different things")
    # was a gap — now the standing invariant: a conversation carrying a
    # limit card implies the node carries a freeze (the exact contradiction
    # the user reported).
    check("consistency · the card and the node state never disagree",
          _card_and_state_agree)


# ══════════════════════════════════════════════════════════════════════════ §4
#
# The fix (9b79281) widened what can freeze an agent, and a freeze is a HARD
# STOP: the node runs nothing until a human presses ▶. So the second question
# is the one a widened detector always owes — what ELSE now freezes — plus the
# one nobody asked when there was no button: does ▶ actually work on this kind?

def sec_attack_the_fix() -> None:
    print("\n§4  the fix, attacked — false positives and the way out")

    # ── a benign synthetic record must not fail the turn ────────────────────
    slug_b, nid_b = probe_org()

    def _benign_synthetic():
        set_mode("benign-synthetic")
        run_turn(slug_b, nid_b)
        n = node(slug_b, nid_b)
        assert not n.get("frozen"), f"froze on a non-limit synthetic: {n['frozen']}"
        rows = (store.load_org(slug_b).d.get("turn_error_log") or {}).get(nid_b) or []
        assert not rows, f"booked a turn failure for a benign synthetic: {rows}"
    check("false-positive · '<synthetic> · No response requested.' (the CLI "
          "writes it constantly) neither freezes nor fails the turn — the "
          "capture is gated on the predicate, not on the flags",
          _benign_synthetic)

    # ── the agent's OWN short answer, riding the result event ───────────────
    # In stream-json the result event's `result` IS the assistant's final text.
    # The <200-char fallback therefore reads every short answer an agent gives.
    slug_a, nid_a = probe_org()

    def _own_answer_about_limits():
        set_mode("plain", reply="Done — raised the rate limit to 100/min; "
                                "it resets nightly.")
        run_turn(slug_a, nid_a)
        n = node(slug_a, nid_a)
        assert not n.get("frozen"), (
            "an agent's own 57-character answer froze it: the result-text "
            "fallback cannot tell the CLI's limit card from a short reply that "
            f"happens to say 'limit' and 'resets' — {n['frozen']}")
    # was a gap: the <200-char bound alone let an agent's own 57-char answer
    # freeze it (and _parse_limit_reset scraped "nightly" out of the prose as
    # the reset time). Fixed 2026-08-05: the flagless result-text fallback
    # now ALSO requires `_parse_limit_reset_ts` to return a real timestamp —
    # the machine marker the CLI's card always carries and prose never does.
    check("false-positive · a short answer that merely MENTIONS a limit does "
          "not freeze its author", _own_answer_about_limits)

    # ── a transient per-minute 429, if the CLI ever surfaces one ────────────
    slug_r, nid_r = probe_org()
    RATE = ("API Error: 429 rate_limit_error — Number of request tokens has "
            "exceeded your per-minute rate limit")

    def _rate_limit_shape():
        set_mode("synthetic", limit_text=RATE)
        run_turn(slug_r, nid_r)
        fz = node(slug_r, nid_r).get("frozen")
        if not fz:
            return                       # not detected at all — nothing to say
        assert fz.get("until_ts") or fz.get("until"), (
            "a per-minute rate limit freezes the node with NO reset time, so "
            "auto_resume has nothing to schedule on and the node waits for a "
            f"human — {fz}")
    # was a gap: a rate-limit-class text (no reset marker) froze with
    # until=None/until_ts=None — nothing but a human ▶ could clear it. Fixed
    # 2026-08-05: a reset-less LIMIT freeze is stamped with a ~5-minute probe
    # time at freeze time ("unknown — probing again in ~5 min"), so
    # auto_resume schedules it through its normal path; a failed probe
    # re-freezes, worst case one try per ~5 min. (Belt-and-braces: the
    # auto_resume loop also retries pre-existing reset-less limit records.)
    check("rate-limit · a freeze with no parseable reset time is not a dead "
          "end", _rate_limit_shape)

    # ── the way out: ▶ resume on a freeze of the NEW kind ───────────────────
    slug_x, nid_x = probe_org()

    def _resume_works():
        set_mode("synthetic")
        run_turn(slug_x, nid_x, "the message that was interrupted")
        fz = node(slug_x, nid_x).get("frozen")
        assert fz, "precondition: the synthetic shape must freeze"
        assert fz.get("limit") is True, f"not tagged as a usage limit: {fz}"
        texts = fz.get("resume_texts") or []
        assert any("the message that was interrupted" in t for t in texts), (
            f"the interrupted message was not kept for replay: {texts}")
        set_mode("plain")               # the limit has 'reset'
        resumed = supervisor.resume_frozen(slug_x)
        assert resumed == [nid_x], f"▶ resume did not pick up the node: {resumed}"
        assert not node(slug_x, nid_x).get("frozen"), "still frozen after ▶"
    check("resume · ▶ clears a synthetic-shape freeze and replays the message "
          "the limit ate (the freeze kind is tagged limit:true, so resume_frozen "
          "owns it rather than deferring to a mechanism that does not exist)",
          _resume_works)

    # ── what state the node is REALLY in after the last network attempt ─────
    slug_n, nid_n = probe_org()
    NETERR = "API Error: fetch failed (ECONNREFUSED 127.0.0.1:443)"

    def _terminal_network_failure_leaves_the_node_unfrozen():
        """A peer read supervisor.py:2263-2268 and reported that its message —
        "resume manually (▶ or new mail)" — names an escape hatch that does
        not work, since a FROZEN node accepts mail and starts nothing. Half
        right, and the wrong half: measured here, the terminal attempt writes
        NO freeze (the record is only written while run <= NET_RETRY_MAX), so
        the node ends unfrozen. It is ▶ that does nothing at that point —
        resume_frozen finds no record to clear — while new mail is the one
        thing that DOES drive it. The sentence is wrong in the opposite
        direction from the report, which is why it is measured and not
        reasoned about."""
        set_mode("iserror", limit_text=NETERR)
        for i in range(supervisor.NET_RETRY_MAX):
            run_turn(slug_n, nid_n, "keeps dropping")
            n = node(slug_n, nid_n)
            fixture(bool(n.get("frozen")),
                    f"attempt {i + 1} did not freeze (run="
                    f"{n.get('net_fail_run')!r})")
            lbl = (n["frozen"].get("until") or "").lower()
            assert "retry" not in lbl and "in ~" not in lbl, (
                "the freeze label PROMISES a retry. Nothing in the backend "
                "performs one unless the org's auto_resume toggle is on — off "
                "is the default and the deliberate policy — and the toggle can "
                "flip after this string is written, so the label must state "
                f"the attempt and leave WHO to the desk: {n['frozen']['until']!r}")
            # ⚠ un-park by clearing the record, NOT with resume_frozen: that
            # SPAWNS a replay turn, which fails on the same dead wire and
            # increments the counter again — the run reached 7 inside four
            # loop passes and sailed past the cap before the loop finished.
            org = store.load_org(slug_n)
            org.nodes[nid_n].pop("frozen", None)
            store.save_org(org)
        run_turn(slug_n, nid_n, "and again")
        n = node(slug_n, nid_n)
        assert (n.get("net_fail_run") or 0) > supervisor.NET_RETRY_MAX, \
            f"never reached the terminal attempt: {n.get('net_fail_run')}"
        assert not n.get("frozen"), (
            "the node is frozen after the terminal attempt, so its own "
            f"message's ▶ half would be the working one: {n['frozen']}")
    check("transient · the attempt PAST the retry cap leaves the node "
          "unfrozen — so 'new mail' resumes it and ▶ is the dead half",
          _terminal_network_failure_leaves_the_node_unfrozen)

    def _replayed_for_real():
        # ▶ hands the replay to a worker thread (supervisor.py:2951), so the
        # measurement has to wait for the turn, not for the call
        for _ in range(150):
            if any("the message that was interrupted" in t for t in served()):
                return
            time.sleep(0.1)
        raise AssertionError(
            f"▶ resumed the node but the message never reached the CLI within "
            f"15 s: {served()}")
    check("resume · and the replay actually reaches the CLI", _replayed_for_real)


def _freeze(slug: str, nid: str, **fz) -> None:
    """Write a freeze record straight onto the doc — §4 is about WHEN the
    timer wakes a node, not about how it came to be frozen, and the CLI
    stand-in cannot produce a second node frozen on a different clock."""
    org = store.load_org(slug)
    org.nodes[nid]["frozen"] = fz
    store.save_org(org)


def sec_wake() -> None:
    """§4 — WHICH frozen node the auto-resume timer wakes, and when.

    Peer report (neoja, 2026-08-10), source-traced and confirmed here: the
    timer gated on `max(until_ts across every frozen node in the org)`, so one
    node parked on a long timer — a weekly fable limit, hours or days out —
    suppressed auto-resume for EVERY other frozen node in that org, including
    a 30-second connection backoff. Two nodes, two clocks, one gate.

    Hermetic: no CLI, no turns — the freeze records are written directly and
    the two decision functions are called by hand.

    ⚠ `resume_frozen` SPAWNS A REPLAY TURN per node it wakes, and this section
    runs before the ones that measure what the CLI stand-in was served. Left
    live, those threads raced the later sections through the shared synthetic
    config and `served.log` — two unrelated checks failed, intermittently,
    with nothing wrong in the code under test. `_run_turn` is stubbed out for
    the section: §4 already proves a resumed node's replay reaches the CLI;
    what is measured HERE is only which nodes got picked.
    """
    print("\n§5 which node wakes, and when (auto_resume_ready) "
          "— runs early: it needs no CLI:")

    real_run_turn = supervisor._run_turn
    supervisor._run_turn = lambda *a, **k: None                  # type: ignore[assignment]
    try:
        _sec_wake_body()
    finally:
        supervisor._run_turn = real_run_turn                     # type: ignore[assignment]


def _sec_wake_body() -> None:
    slug, a = probe_org()
    org = store.load_org(slug)
    b = org.hire(USER, None, "haiku", 20, "slow",
                 add_dirs=[], tools={"bash": False, "web": False, "edit": False,
                                     "subagents": False, "mcp": []},
                 org_visibility="team", charter="the long freeze")["node"]
    org.d["auto_resume"] = True
    store.save_org(org)
    now = time.time()
    _freeze(slug, a, connection=True, until_ts=now - 5,
            until="network interruption — attempt 1/4")
    _freeze(slug, b, limit=True, until_ts=now + 7 * 86400,
            until="weekly limit")

    def _short_freeze_is_not_starved_by_a_long_one():
        ready = supervisor.auto_resume_ready(store.load_org(slug))
        assert a in ready, (
            "a 30-second connection backoff whose time has passed was NOT "
            "woken, because a sibling node in the same org is frozen until "
            "next week. One clock cannot speak for another: the org-wide "
            f"max() gate starves every short freeze behind the longest — {ready}")
        assert b not in ready, f"the long freeze woke early: {ready}"
    check("wake · a due connection backoff is not starved by a sibling frozen "
          "until next week (peer report 2026-08-10)",
          _short_freeze_is_not_starved_by_a_long_one)

    def _the_wake_resumes_only_the_due_node():
        supervisor.resume_frozen(slug, only=supervisor.auto_resume_ready(
            store.load_org(slug)))
        assert not node(slug, a).get("frozen"), "the due node stayed frozen"
        assert node(slug, b).get("frozen"), (
            "waking the due node un-froze the one whose limit has NOT reset — "
            "which is exactly what the org-wide gate existed to prevent, so "
            "the per-node readiness must be paired with a per-node resume")
    check("wake · …and waking it leaves the not-yet-due node frozen",
          _the_wake_resumes_only_the_due_node)

    def _play_still_means_the_whole_org():
        _freeze(slug, a, connection=True, until_ts=time.time() - 5)
        assert set(supervisor.resume_frozen(slug)) == {a, b}, (
            "▶ with no filter must keep its all-at-once meaning: a human "
            "pressing resume has judged the whole org ready, which is a "
            "different claim from a timer's")
    check("wake · ▶ itself still resumes the whole org, time or no time",
          _play_still_means_the_whole_org)

    def _a_node_another_mechanism_owns_is_never_ready():
        _freeze(slug, a, limit=True, until_ts=time.time() - 600)
        org2 = store.load_org(slug)
        org2.nodes[a]["limit_locked"] = True
        # ⚠ the flag needs a lock BEHIND it or the ledger's load hook sweeps it
        # as an orphan (ledger.py ~401, redteam 2026-08-06) — a limit_locked
        # with no fable_lock is an artifact, and the hook is right to clear it.
        # `no_reset` keeps the lock pending instead of releasing on load.
        org2.d["fable_lock"] = {"no_reset": True, "at": "now", "by": a}
        store.save_org(org2)
        fixture(store.load_org(slug).nodes[a].get("limit_locked") is True,
                "the load hook cleared limit_locked — this check needs a node "
                "that is genuinely owned by the fable lock")
        assert a not in supervisor.auto_resume_ready(store.load_org(slug)), (
            "a limit_locked node is one resume_frozen SKIPS, so counting it as "
            "ready re-fires the sweep every 30 s forever while nothing changes")
    check("wake · a node another mechanism owns is never counted ready",
          _a_node_another_mechanism_owns_is_never_ready)

    def _grace_applies_to_the_kind_that_needs_it():
        org3 = store.load_org(slug)
        org3.d.pop("fable_lock", None)          # the load hook then sweeps the flag
        store.save_org(org3)
        org3 = store.load_org(slug)
        org3.nodes[a].pop("limit_locked", None)
        store.save_org(org3)
        t = time.time()
        _freeze(slug, a, limit=True, until_ts=t - 5)
        assert a not in supervisor.auto_resume_ready(store.load_org(slug)), (
            "a LIMIT reset time is the API's claim about someone else's clock; "
            "waking on the dot re-freezes. The minute of grace is the point")
        _freeze(slug, a, limit=True, until_ts=t - 61)
        assert a in supervisor.auto_resume_ready(store.load_org(slug)), \
            "a limit past reset+1min must wake"
        _freeze(slug, a, connection=True, until_ts=t - 1)
        assert a in supervisor.auto_resume_ready(store.load_org(slug)), (
            "a connection backoff is OUR OWN timer measured from our own "
            "failure — padding it makes the node wait longer than the "
            "'retry in ~30s' label it already showed the user")
    check("wake · the minute of grace is a LIMIT's, not a connection backoff's",
          _grace_applies_to_the_kind_that_needs_it)

    def _a_reset_less_freeze_still_probes():
        org4 = store.load_org(slug)
        org4.d["auto_resume_last"] = 0.0
        store.save_org(org4)
        _freeze(slug, a, limit=True)          # no until_ts at all
        assert a in supervisor.auto_resume_ready(store.load_org(slug)), (
            "a reset-less limit freeze is probed on the 5-minute floor rather "
            "than left for a human forever (redteam gap 2026-08-05) — the "
            "per-node rewrite must not have dropped that branch")
        org5 = store.load_org(slug)
        org5.d["auto_resume_last"] = time.time()
        store.save_org(org5)
        assert a not in supervisor.auto_resume_ready(store.load_org(slug)), \
            "the 5-minute probe floor stopped applying"
    check("wake · a reset-less freeze still probes on the 5-minute floor",
          _a_reset_less_freeze_still_probes)


def _flat(tree: dict) -> list[dict]:
    out: list[dict] = []

    def walk(n: dict) -> None:
        out.append(n)
        for k in n.get("children") or []:
            walk(k)
    for r in tree.get("roots") or tree.get("nodes") or []:
        walk(r)
    return out


# ═════════════════════════════════════════════════════════════════════════ main

def main() -> None:
    print("═══ usage-limit freeze — the shape the CLI actually reports ═══")
    sec_detect()
    sec_wake()
    if not shutil.which("node"):
        note("node is not on PATH — §2/§3 skipped (they need the CLI stand-in)")
    else:
        sec_shapes()
        sec_reader()
        sec_attack_the_fix()

    print(f"\n{'═' * 70}\n{PASS} checks passed, {len(FAIL)} failed, "
          f"{len(GAPS)} gaps")
    for label, tb in FAIL:
        print(f"\nFAIL  {label}\n{tb}")
    if GAPS:
        print("\n⚑ GAPS — measured, currently true, reported to the implementer:")
        for label, why, detail in GAPS:
            print(f"\n  ⚑ {label}\n    measured: {detail}\n    {why}")
    if NOTES:
        print("\nnotes:")
        for m in NOTES:
            print(f"  · {m}")
    try:
        shutil.rmtree(_TMP, ignore_errors=True)
    except OSError:
        pass
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
