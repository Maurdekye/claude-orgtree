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
    §6  WHERE a freeze's reset timestamp comes from, and what it is allowed to
        cost — the bands, the provenance gates and the api_fallback window
        (D-133, and ten rounds of adversarial review behind it).

Hermetic-ish: throwaway ORGTREE_DATA + HOME, no port, no Docker, no real CLI,
no network. §2 spawns `node` (the stand-in) — skipped with a note if absent.

    python backend/tests/test_limit_freeze.py [-v]
"""

from __future__ import annotations

import json
import re
import os
import shutil
import sys
import threading
import tempfile
import time
import traceback
from typing import Any

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

from orgtree import limits, sandbox as sbx, store, supervisor    # noqa: E402
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
  // ── §7's three shapes. All exit NONZERO; they differ only in what the
  // CLI managed to say first, which is the whole of _died_in_flight's test.
  // ⚠ fs.writeSync(1) and not say(): process.stdout to a PIPE is async in
  // node, and process.exit() truncates whatever is still queued. Written
  // with say() the assistant event raced the exit and arrived only
  // sometimes — which would have made `started` flap and the section
  // intermittently green for the wrong reason.
  if (cfg.mode === 'died-in-flight' || cfg.mode === 'died-with-stderr') {
    const msg = { role: 'assistant', model: 'fake',
                  content: [{ type: 'text', text: 'on it — first I will' }],
                  usage: { input_tokens: 1000 } }
    fs.writeSync(1, JSON.stringify({ type: 'assistant', message: msg }) + '\n')
    record({ type: 'assistant', message: msg })
    // died-in-flight: THE INCIDENT (2026-08-21). The model was answering,
    // the wire dropped, and the CLI went down with an exit code and nothing
    // else — no result event, no stderr, no errors[].
    // died-with-stderr: the same death WITH evidence. Must stay terminal.
    if (cfg.mode === 'died-with-stderr') {
      fs.writeSync(2, 'Error: ENOSPC: no space left on device\n')
    }
    process.exit(1)
  }
  if (cfg.mode === 'hang') {
    // §9 door 1: the CLI goes silent mid-turn and never reaches a boundary.
    // Nothing more is written, so the idle watchdog is the only thing that
    // can end this turn — which is exactly the door being measured.
    return
  }
  if (cfg.mode === 'dead-on-arrival') {
    // a bad argv, an unreadable config, a charter too large to send: the CLI
    // dies before the model ever speaks. Byte-identical failure from the
    // outside — an exit code and silence — and it must NOT be retried.
    process.exit(1)
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

# ══════════════════════════════════════════════════════════════════ §6 timing
# User ruling 2026-08-18: "all forms of usage freeze should have a timestamp
# associated … that way api key fallback usage never accidentally stays
# permanent and rings up a massive unintended bill."
#
# The number under test is money. `api_fallback` bills the ORG'S OWN API KEY
# for the length of the window a freeze opens, and that window is priced off
# the freeze's reset timestamp — so every way the timestamp can be wrong is a
# way to overspend, and every source it can come from needs a band.
#
# Hermetic: the usage readout is injected into `limits`' cache by hand. No
# network, no CLI, no token — a suite that reached the real endpoint would
# read a different account on every machine and spend a request per run.


def _iso(offset_s):
    import datetime as _d
    return (_d.datetime.now(_d.timezone.utc)
            + _d.timedelta(seconds=offset_s)).isoformat()


def _readout(*lanes) -> None:
    """Install a synthetic usage readout as the cached one."""
    limits._cache.update(at=time.time(), data={
        "available": True, "plan": "max",
        "limits": [{"kind": k, "group": g, "percent": p, "severity": sv,
                    "resets_at": _iso(r), "is_active": act, "model": m}
                   for k, g, p, sv, r, act, m in lanes]})


class _FakeOrg:
    """Just enough Org for the pure decision functions: `.d` and `.nodes`.
    (`spawn_env`, `bills_the_key`, `_resumable` and `auto_resume_ready` read
    nothing else — a real org here would need a data root and a hire.)"""

    def __init__(self, **d):
        self.d = d
        self.nodes: dict[str, Any] = {}


def _with_window(slug, nid):
    """The org with a fallback window open — as a REAL limit on some other
    node would leave it. Used to prove the fast-wake path does not pick up a
    capped untrusted freeze."""
    o = store.load_org(slug)
    o.d["api_key"] = "sk-test"
    o.d["api_fallback"] = True
    o.d["api_fallback_until"] = time.time() + 3600
    return o


def sec_reset_timing() -> None:
    """⚠ Wrapped: an exception raised OUTSIDE a `check()` (a fixture, a
    `probe_org()` hiccup) used to leave the synthetic readout installed in
    `limits._cache`, where §2–§4's real freeze path would read it as the
    account's true standing (redteam 2026-08-18)."""
    try:
        _sec_reset_timing_body()
    finally:
        limits.invalidate()


def _sec_reset_timing_body() -> None:
    print("\n§6 · where a freeze's reset timestamp comes from, and its bands:")
    now = time.time()
    # the suite must never reach the live endpoint: HOME/USERPROFILE are
    # redirected before orgtree is imported, so there are no credentials to
    # find. Asserted rather than assumed — a future suite that forgets the
    # redirect would otherwise start rotating the host's OAuth token.
    check("hermetic · no host credentials are visible to this suite", lambda: (
        None if not limits.available()
        else (_ for _ in ()).throw(AssertionError(
            "the usage readout can reach the real account from a test"))))

    # ---- the prose classifier -------------------------------------------
    check("classify · a session limit stays a session limit even when it "
          "names a model (FABLE-1 in another costume)", lambda: (
        None if limits.classify("session limit for Fable 5 reached")
        == ("session", None)
        else (_ for _ in ()).throw(AssertionError(
            limits.classify("session limit for Fable 5 reached")))))
    check("classify · the real Fable-tier wording → the model's weekly pool",
          lambda: (
        None if limits.classify(
            "You've reached your Fable 5 limit. Run /usage-credits to continue")
        == ("weekly_scoped", "fable")
        else (_ for _ in ()).throw(AssertionError("fable tier misread"))))
    check("classify · 'weekly' → the unscoped weekly lane", lambda: (
        None if limits.classify("Claude usage limit reached (weekly)")
        == ("weekly_all", None)
        else (_ for _ in ()).throw(AssertionError("weekly misread"))))
    check("classify · a bare limit names no lane", lambda: (
        None if limits.classify("Claude AI usage limit reached") == (None, None)
        else (_ for _ in ()).throw(AssertionError("invented a lane"))))

    # ---- lane selection out of the readout -------------------------------
    _readout(("session", "session", 15, "normal", 2 * 3600, False, None),
             ("weekly_all", "weekly", 65, "normal", 5 * 3600, False, None),
             ("weekly_scoped", "weekly", 99, "critical", 6 * 3600, True,
              "Fable"))
    check("reset_for · the lane the prose names answers", lambda: (
        None if limits.reset_for("weekly limit reached")[1] == "usage:weekly_all"
        else (_ for _ in ()).throw(AssertionError(
            limits.reset_for("weekly limit reached")))))
    check("reset_for · a scoped lane is matched on the model name", lambda: (
        None if limits.reset_for("You've reached your Fable 5 limit")[1]
        == "usage:weekly_scoped"
        else (_ for _ in ()).throw(AssertionError("scoped lane missed"))))
    check("reset_for · an unnamed lane takes the SOONEST reset, not the "
          "is_active one — guessing short costs one re-freeze, guessing long "
          "costs money (user ruling 2026-08-18)", lambda: (
        None if limits.reset_for("Claude AI usage limit reached")[1]
        == "usage:session"
        else (_ for _ in ()).throw(AssertionError(
            limits.reset_for("Claude AI usage limit reached")))))

    # ---- the bands --------------------------------------------------------
    _readout(("session", "session", 99, "critical", -600, True, None))
    check("reset_for · a reset already in the past is not a horizon", lambda: (
        None if limits.reset_for("usage limit reached") == (None, "")
        else (_ for _ in ()).throw(AssertionError("believed a stale reset"))))
    _readout(("session", "session", 99, "critical", 20 * 3600, True, None))
    check("reset_for · a 5-hour lane cannot reset 20 hours out", lambda: (
        None if limits.reset_for("usage limit reached") == (None, "")
        else (_ for _ in ()).throw(AssertionError("lane band not applied"))))

    # a named lane whose own reset is not believable must not sink the
    # answer — the ruling is "always a timestamp", and another lane's is
    # right there
    _readout(("session", "session", 99, "critical", -600, True, None),
             ("weekly_all", "weekly", 70, "normal", 4 * 3600, False, None))
    check("reset_for · a stale named lane falls through to a believable one",
          lambda: (
        None if limits.reset_for("session limit reached")[1]
        == "usage:weekly_all"
        else (_ for _ in ()).throw(AssertionError(
            limits.reset_for("session limit reached")))))

    # the correction pass must actually RE-READ: a limit that just fired
    # changed the standing, so an entry the warm loop filled seconds earlier
    # predates the event. Served from the ordinary 30 s cache the pass would
    # hand back the very number the freeze already stamped from.
    _readout(("session", "session", 99, "critical", 3 * 3600, True, None))
    _calls = [0]

    def _count_fetch(force=False, max_age=None):
        _calls.append(max_age)
        _calls[0] += 1
        return limits.cached() or {"available": False}

    _rf, limits.fetch = limits.fetch, _count_fetch
    try:
        check("re-read · the correction pass tightens the cache window "
              "instead of accepting a 30-second-old readout", lambda: (
            None if (limits.reset_for("usage limit reached", allow_fetch=True)
                     and _calls[0] == 1
                     and _calls[-1] == limits.REREAD_MAX_AGE
                     and limits.REREAD_MAX_AGE < limits.CACHE_TTL)
            else (_ for _ in ()).throw(AssertionError(_calls))))
    finally:
        limits.fetch = _rf

    # ---- the freeze path never blocks on the network ----------------------
    limits.invalidate()
    _boom = [0]

    def _explode(*a, **k):
        _boom[0] += 1
        raise AssertionError("the freeze path fetched under the lock")

    _real_fetch, limits.fetch = limits.fetch, _explode
    try:
        check("stamp · the default resolver answers from cache only — a cold "
              "cache is 'no idea', never a fetch", lambda: (
            None if supervisor._limit_reset_ts("usage limit reached")
            == (None, "") and _boom[0] == 0
            else (_ for _ in ()).throw(AssertionError("it fetched"))))
    finally:
        limits.fetch = _real_fetch

    # …and the property that actually matters — no network under DOC_LOCK —
    # is structural, so it is guarded at the source. The freeze site must not
    # ask for a fetch, and the correction pass must do its fetching BEFORE it
    # takes the lock. (Redteam 2026-08-18: the runtime check above cannot see
    # either of those, and would keep passing if the freeze path started
    # fetching under the lock tomorrow.)
    _sup = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "..", "orgtree", "supervisor.py"),
                encoding="utf-8").read()
    _api = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "..", "orgtree", "api.py"),
                encoding="utf-8").read()
    _code = "\n".join(ln for ln in _sup.splitlines()
                      if not ln.lstrip().startswith("#"))

    def _freeze_site_does_not_fetch():
        import inspect
        i = _code.find("fz[\"limit\"] = True")
        fixture(i > 0, "the freeze site moved — re-read this check")
        # through the api_fallback window write, which is where the block
        # actually ends — a tight window slides off the end unnoticed
        j = _code.find("store.save_org(o2)", i)
        fixture(j > i, "the freeze block no longer ends in a save")
        seg = _code[i:j]
        assert "_limit_reset_ts(" in seg, "the freeze no longer times itself"
        assert not re.search(r"allow_fetch\s*=\s*(True|[^F\s)])", seg), (
            "the freeze site runs inside `with store.DOC_LOCK:` and the usage "
            "endpoint routinely takes over a second — a fetch here stalls "
            "every org on the backend, not just this one")
        # …and the default it relies on is really cache-only (the lexical
        # check above cannot see a flipped default — redteam 2026-08-18)
        sig = inspect.signature(supervisor._limit_reset_ts)
        assert sig.parameters["allow_fetch"].default is False, (
            "the resolver now fetches by DEFAULT, so every freeze does a "
            "network round trip under the document lock")

    def _the_correction_fetches_before_it_locks():
        i = _code.find("def _refresh_freeze_reset(")
        fixture(i > 0, "the correction pass moved — re-read this check")
        body = _code[i:i + 3000]
        f, lock = body.find("allow_fetch=True"), body.find("with store.DOC_LOCK")
        fixture(f > 0 and lock > 0, "the pass no longer fetches, or no longer locks")
        assert f < lock, ("the re-read must happen BEFORE the document lock "
                          "is taken, or it holds the whole backend for the "
                          "length of an HTTPS round trip")
    check("lock · the freeze site never asks for a fetch (structural)",
          _freeze_site_does_not_fetch)
    check("lock · the correction pass fetches before it locks (structural)",
          _the_correction_fetches_before_it_locks)

    # The strongest version of the same property: drive a REAL limit turn
    # through the real loop and watch who calls `fetch`. The freeze runs on
    # the turn's own thread while holding DOC_LOCK; only the correction pass,
    # on its own named thread, is allowed to reach the network (redteam
    # 2026-08-18 — the structural guards above are lexical, and this is not).
    if shutil.which("node"):
        _fetchers: list[str] = []
        _rf2, limits.fetch = limits.fetch, (
            lambda *a, **k: (_fetchers.append(threading.current_thread().name)
                             or {"available": False, "error": "probe"}))
        try:
            _slug, _nid = probe_org()
            # a wording with NO parseable marker, so the correction pass is
            # forced to consult the readout — otherwise prose answers, nobody
            # fetches, and "no bad fetcher" would be vacuously true
            set_mode("iserror", limit_text="Claude AI usage limit reached")
            run_turn(_slug, _nid)
            _deadline = time.time() + 5
            while time.time() < _deadline and not _fetchers:
                time.sleep(0.05)
            _bad = [t for t in _fetchers if not t.startswith("usage-reset-")]
            check("lock · a REAL limit turn reaches the network only from the "
                  "correction thread — never from the freeze itself, which "
                  "holds DOC_LOCK", lambda: (
                None if node(_slug, _nid).get("frozen") and _fetchers
                and not _bad
                else (_ for _ in ()).throw(AssertionError(
                    "froze=%s fetchers=%s" % (
                        bool(node(_slug, _nid).get("frozen")), _fetchers)))))
        finally:
            limits.fetch = _rf2
    else:
        note("node is absent — the real-turn lock probe was skipped")

    # ⚠ THE GAP THAT SHIPPED A REGRESSION (redteam 2026-08-18): `reset_src`
    # was only ever asserted against a hand-built freeze record, so nothing
    # noticed that a REAL freeze arriving by the CLI's `<synthetic>` route was
    # being classed as agent-authored and throwing away the epoch the CLI had
    # just published. Drive the real loop, read the real record.
    if shutil.which("node"):
        _ep = int(time.time()) + 3 * 86400
        for _mode, _want in (("iserror", "text"), ("synthetic", "text")):
            _s2, _n2 = probe_org()
            set_mode(_mode,
                     limit_text="Claude AI usage limit reached|%d" % _ep)
            run_turn(_s2, _n2)
            check("e2e · a %s limit keeps the epoch the CLI published "
                  "(reset_src=%s)" % (_mode, _want), (
                lambda s2=_s2, n2=_n2, w=_want: (
                    None if (node(s2, n2).get("frozen") or {}).get("reset_src")
                    == w and abs(float((node(s2, n2).get("frozen")
                                        or {})["until_ts"]) - _ep) < 2
                    else (_ for _ in ()).throw(AssertionError(
                        node(s2, n2).get("frozen"))))))

    # ---- text vs readout precedence ---------------------------------------
    _readout(("session", "session", 99, "critical", 3 * 3600, True, None))
    _ep = int(now) + 1800
    check("stamp · an explicit epoch in the prose wins", lambda: (
        None if supervisor._limit_reset_ts("usage limit reached|%d" % _ep)
        == (float(_ep), "text")
        else (_ for _ in ()).throw(AssertionError("prose epoch ignored"))))
    check("stamp · unparseable prose falls through to the readout", lambda: (
        None if supervisor._limit_reset_ts(
            "Claude AI usage limit reached")[1] == "usage:session"
        else (_ for _ in ()).throw(AssertionError("no readout fallback"))))

    # ---- the window this prices -------------------------------------------
    check("window · a 15-minute floor (a 5-minute probe freeze must still "
          "get a turn out)", lambda: (
        None if abs(supervisor._fallback_window_until(now + 60, now)
                    - (now + 900)) < 1
        else (_ for _ in ()).throw(AssertionError("floor missing"))))
    check("window · no timestamp at all still bounds to the floor", lambda: (
        None if abs(supervisor._fallback_window_until(None, now)
                    - (now + 900)) < 1
        else (_ for _ in ()).throw(AssertionError("unbounded on None"))))
    check("window · CEILING — a 60-day 'reset' cannot bill the key for 60 "
          "days (the unintended-bill guard)", lambda: (
        None if abs(supervisor._fallback_window_until(now + 60 * 86400, now)
                    - (now + supervisor.FALLBACK_MAX_WINDOW)) < 1
        else (_ for _ in ()).throw(AssertionError("no ceiling"))))
    check("window · a real weekly reset survives the ceiling", lambda: (
        None if abs(supervisor._fallback_window_until(now + 6.9 * 86400, now)
                    - (now + 6.9 * 86400)) < 1
        else (_ for _ in ()).throw(AssertionError("clamped a real weekly"))))

    # ---- the off-lock correction pass -------------------------------------
    slug, nid = probe_org()
    _later = now + 4 * 3600

    def _stamped(win):
        org = store.load_org(slug)
        org.nodes[nid]["frozen"] = {
            "at": "x", "limit": True, "until_ts": now + 300,
            "until": "unknown — probing again in ~5 min", "reset_src": "probe"}
        org.d["api_key"] = "sk-test"
        org.d["api_fallback"] = True
        if win is not None:
            org.d["api_fallback_until"] = win
        store.save_org(org)

    _real = supervisor._limit_reset_ts
    # **kw so a new argument on the real resolver does not silently turn this
    # stub into a TypeError the retry loop swallows (caught 2026-08-18)
    supervisor._limit_reset_ts = lambda blob, **kw: (_later, "usage:session")
    try:
        _w = supervisor._fallback_window_until(now + 300, now)
        _stamped(_w)
        check("refresh · the correction rewrites the freeze it stamped",
              lambda: (
            None if supervisor._refresh_freeze_reset(
                slug, nid, "limit", now + 300, _w)
            and abs(float(store.load_org(slug).nodes[nid]["frozen"]["until_ts"])
                    - _later) < 1
            and store.load_org(slug).nodes[nid]["frozen"]["reset_src"]
            == "usage:session"
            else (_ for _ in ()).throw(AssertionError("no correction"))))
        check("refresh · and re-prices the window it opened", lambda: (
            None if abs(float(store.load_org(slug).d["api_fallback_until"])
                        - _later) < 2
            else (_ for _ in ()).throw(AssertionError(
                store.load_org(slug).d.get("api_fallback_until")))))

        _stamped(_w)
        _o = store.load_org(slug)
        _o.nodes[nid]["frozen"]["until_ts"] = now + 999   # someone else moved it
        store.save_org(_o)
        check("refresh · a freeze re-stamped by someone else is not ours to "
              "move (its WINDOW still is — the two are owned separately)",
              lambda: (
            None if supervisor._refresh_freeze_reset(
                slug, nid, "limit", now + 300, _w)
            and float(store.load_org(slug).nodes[nid]["frozen"]["until_ts"])
            == now + 999
            and abs(float(store.load_org(slug).d["api_fallback_until"])
                    - _later) < 2
            else (_ for _ in ()).throw(AssertionError("stomped a peer"))))

        _stamped(_w)
        _o = store.load_org(slug)
        _o.d["api_fallback_until"] = now + 12345         # a later freeze's
        store.save_org(_o)
        supervisor._refresh_freeze_reset(slug, nid, "limit", now + 300, _w)
        check("refresh · a window it did not open is left alone", lambda: (
            None if float(store.load_org(slug).d["api_fallback_until"])
            == now + 12345
            else (_ for _ in ()).throw(AssertionError("stomped a window"))))

        _stamped(_w)
        _o = store.load_org(slug)
        _o.d.pop("api_fallback")                         # user turned it off
        store.save_org(_o)
        supervisor._refresh_freeze_reset(slug, nid, "limit", now + 300, _w)
        check("refresh · a fallback switched off mid-flight keeps its window "
              "untouched (the pass re-prices, it must not re-open)", lambda: (
            None if float(store.load_org(slug).d["api_fallback_until"]) == _w
            and not store.load_org(slug).d.get("api_fallback")
            else (_ for _ in ()).throw(AssertionError(
                store.load_org(slug).d.get("api_fallback_until")))))

        _stamped(_w)
        _o = store.load_org(slug)
        _o.nodes[nid]["frozen"] = None          # the user hit ▶ mid-flight
        store.save_org(_o)
        supervisor._refresh_freeze_reset(slug, nid, "limit", now + 300, _w)
        check("refresh · a node resumed mid-flight still gets its WINDOW "
              "re-priced — the likeliest thing to happen in that second must "
              "not leave an over-long window with no owner", lambda: (
            None if abs(float(store.load_org(slug).d["api_fallback_until"])
                        - _later) < 2
            else (_ for _ in ()).throw(AssertionError(
                store.load_org(slug).d.get("api_fallback_until")))))

        supervisor._limit_reset_ts = lambda blob, **kw: (now + 330,
                                                        "usage:session")
        _stamped(_w)
        check("refresh · a move under a minute is not worth a write", lambda: (
            None if not supervisor._refresh_freeze_reset(
                slug, nid, "limit", now + 300, _w)
            else (_ for _ in ()).throw(AssertionError("churned the doc"))))
    finally:
        supervisor._limit_reset_ts = _real

    # ---- R2·F1 the UNNAMED lane is capped too -----------------------------
    _readout(("weekly_all", "weekly", 65, "normal", 6 * 86400, False, None))
    check("cap · an unnamed limit is not answered from a weekly lane six days "
          "out — that is the ruling's 'assume the shortest', and the branch "
          "it was missing from", lambda: (
        None if limits.reset_for("Claude AI usage limit reached") == (None, "")
        else (_ for _ in ()).throw(AssertionError(
            limits.reset_for("Claude AI usage limit reached")))))
    _readout(("weekly_all", "weekly", 65, "normal", 2 * 3600, False, None))
    check("cap · …but a weekly lane resetting within the session lane's "
          "reach is a fine answer for an unnamed limit", lambda: (
        None if limits.reset_for("Claude AI usage limit reached")[1]
        == "usage:weekly_all"
        else (_ for _ in ()).throw(AssertionError("over-tight cap"))))

    # ---- R8 · the guards 89 mutations found unpinned -----------------------
    # Round 8 mutated the feature 89 ways and the suite missed 22 of them.
    # Every check below kills at least one of those mutants; several sit on
    # guards whose own comments name the incident they were written for.

    # ① spawn_env's api_fallback gate — the single load-bearing money seam.
    # Three mutations were green: always-inject (the option silently becomes a
    # permanent key lane), never-inject (a paid window that does nothing), and
    # dropping the sandbox guard (the key lands in a host-side docker exec).
    _k = "sk-ant-spawn-probe"
    _shut = _FakeOrg(slug="zz", api_key=_k, api_fallback=True)
    _open = _FakeOrg(slug="zz", api_key=_k, api_fallback=True,
                     api_fallback_until=time.time() + 3600)
    _perm = _FakeOrg(slug="zz", api_key=_k)
    _sbxd = _FakeOrg(slug="zz", api_key=_k, kiosk={"sandbox": True})
    check("spawn · a fallback org with the window SHUT bills the "
          "subscription — no key in the turn's environment", lambda: (
        None if "ANTHROPIC_API_KEY" not in supervisor.spawn_env(_shut)
        else (_ for _ in ()).throw(AssertionError("permanent key lane"))))
    check("spawn · …and gets the key exactly while the window is open",
          lambda: (
        None if supervisor.spawn_env(_open).get("ANTHROPIC_API_KEY") == _k
        else (_ for _ in ()).throw(AssertionError("paid window, no key"))))
    check("spawn · a permanent-key org gets it always", lambda: (
        None if supervisor.spawn_env(_perm).get("ANTHROPIC_API_KEY") == _k
        else (_ for _ in ()).throw(AssertionError("keyless"))))
    check("spawn · a SANDBOXED org never gets it host-side — the container "
          "owns its own credential (spawn_env's own docstring)", lambda: (
        None if "ANTHROPIC_API_KEY" not in supervisor.spawn_env(_sbxd)
        else (_ for _ in ()).throw(AssertionError("leaked into docker exec"))))

    # ② the on_fallback cluster. Three mutations, three misses, one hole: the
    # RECORD field was referenced by no test at all.
    def _org_with(fzd, window=True):
        o = _FakeOrg(slug="zz", api_key="sk", api_fallback=True,
                     api_fallback_until=time.time() + (3600 if window else -1),
                     auto_resume=True)
        o.nodes = {"n": {"state": "live", "frozen": dict(fzd)}}
        return o
    check("lane · a freeze earned ON the key lane is still resumable — "
          "un-exempting `on_fallback` in _resumable makes ▶ skip the node "
          "forever (the pre-№41 spend-freeze trap)", lambda: (
        None if supervisor._resumable(
            {"state": "live", "frozen": {"limit": True, "on_fallback": True}})
        is not None
        else (_ for _ in ()).throw(AssertionError("▶ would skip it"))))
    check("lane · …and an open window does NOT insta-wake it into the same "
          "wall it just hit", lambda: (
        None if "n" not in supervisor.auto_resume_ready(
            _org_with({"limit": True, "on_fallback": True,
                       "until_ts": time.time() + 3600}))
        else (_ for _ in ()).throw(AssertionError("woken into the wall"))))
    check("lane · …while a SUBSCRIPTION-lane freeze beside it is woken at "
          "once, which is what the window is for", lambda: (
        None if "n" in supervisor.auto_resume_ready(
            _org_with({"limit": True, "until_ts": time.time() + 3600}))
        else (_ for _ in ()).throw(AssertionError("the window did nothing"))))

    # ②b …and the RECORD's value comes from the lane the turn actually ran
    # on. Both directions are drivable; the third case — a window that OPENS
    # mid-turn, which is what made re-reading `api_fallback_active` wrong —
    # is not, so it is pinned at the source.
    if shutil.which("node"):
        for _win, _want, _why in (
                (True, True, "a turn spawned INSIDE a window froze on the key "
                             "lane, so it must wait out its own reset"),
                (False, False, "a turn spawned on the subscription froze on "
                               "the subscription, whatever the org looks like "
                               "now — mismarking it makes the fast-wake skip "
                               "it, and it sleeps for hours beside a paid, "
                               "open, unused key window")):
            _ls, _ln = probe_org()
            _lo = store.load_org(_ls)
            _lo.d["api_key"] = "sk-test"
            _lo.d["api_fallback"] = True
            if _win:
                _lo.d["api_fallback_until"] = time.time() + 3600
            store.save_org(_lo)
            set_mode("iserror", limit_text="Claude AI usage limit reached")
            run_turn(_ls, _ln)
            _fzl = (store.load_org(_ls).nodes[_ln].get("frozen") or {})
            check("lane · %s" % _why, (
                lambda f=_fzl, w=_want: (
                    None if bool(f.get("on_fallback")) is w
                    else (_ for _ in ()).throw(AssertionError(f)))))

    def _the_record_takes_the_spawn_lane():
        j = _code.find("fz[\"limit\"] = True")
        fixture(j > 0, "the freeze site moved — re-read this check")
        seg = _code[j:_code.find("store.save_org(o2)", j)]
        rhs = re.findall(r'fz\["on_fallback"\]\s*=\s*([^\n]+)', seg)
        fixture(len(rhs) == 2, "expected both freeze branches to record it")
        assert all(r.strip() == "on_fallback_key" for r in rhs), (
            "the flag must be the lane THIS turn ran on, captured at spawn — "
            "re-reading `api_fallback_active` at freeze time is the bug, and "
            "hardcoding True is the same bug with no window at all: %r" % rhs)
    check("lane · both freeze branches record the SPAWN lane (structural — "
          "a window opening mid-turn is not drivable from a test)",
          _the_record_takes_the_spawn_lane)

    # ③ `_candidate`'s per-entry lane band, reached the way the mutation did:
    # through a NAMED blob (the existing check uses an unnamed one, which
    # exercises `_within` instead and leaves this path bare).
    _readout(("session", "session", 99, "critical", 20 * 3600, True, None))
    check("cap · a named SESSION limit is not answered by a session lane "
          "claiming to reset 20 hours out — same money shape as the "
          "live-caught 23-hour window, arriving via the readout", lambda: (
        None if limits.reset_for("You've hit your session limit") == (None, "")
        else (_ for _ in ()).throw(AssertionError(
            limits.reset_for("You've hit your session limit")))))
    limits.invalidate()

    # ④ the org-wide fable trigger's session exclusion — FABLE-1 itself.
    check("fable · a session limit that MENTIONS Fable does not fire the "
          "org-wide escalation (FABLE-1: under `dissolve` it archives every "
          "fable node's subtree)", lambda: (
        None if not supervisor._looks_like_fable_tier_limit(
            "You've reached your Fable 5 session limit")
        and supervisor._looks_like_fable_tier_limit(
            "You've reached your Fable 5 limit.")
        else (_ for _ in ()).throw(AssertionError("FABLE-1 is back"))))

    # ⑤ classify: the ordering claim and both _TIER_RE guards were vacuous —
    # the blob in the "session wins" check does not even match _TIER_RE, so
    # reordering the branches left it green.
    for _b, _want, _why in (
            ("You've reached your Fable 5 session limit", ("session", None),
             "matches BOTH tests, so it discriminates the order"),
            ("the sonnet-4-5 model is over its limit", (None, None),
             "a hyphenated model id is not a tier limit"),
            ("API Error: overloaded for model fable 5, usage limit reached",
             (None, None), "no possessive anchor, so not a tier limit")):
        check("classify · %s" % _why, (
            lambda b=_b, w=_want: (
                None if limits.classify(b) == w
                else (_ for _ in ()).throw(AssertionError(
                    (b, limits.classify(b)))))))

    # ⑥ the RATE constants, as literals. Round 7 pinned the money ones and
    # left these comparing against themselves.
    # ⚠ this read `"time.time() + 300" in _code`, which is a PREFIX of
    # `+ 3000` — the guard passed on the exact drift it was named for, and a
    # guard that reports green on its own subject is worse than none (redteam
    # round 9). The floor is a module constant now, so the check is a literal.
    check("constants · the self-report cap is 3, the blind probe floor is 5 "
          "minutes, and the outer horizon is 8 days", lambda: (
        None if supervisor.UNTRUSTED_LIMIT_RUNS == 3
        and supervisor.PROBE_FLOOR == 300.0
        and supervisor.REREAD_TRIES == 3
        and limits.MAX_HORIZON == 8 * 86400.0
        and limits.CACHE_TTL == 30.0
        else (_ for _ in ()).throw(AssertionError(
            (supervisor.UNTRUSTED_LIMIT_RUNS, supervisor.PROBE_FLOOR,
             limits.MAX_HORIZON, limits.CACHE_TTL)))))

    # ⑦ /api/usage must not force a fetch — the modal polls, and one cache
    # for two consumers is the invariant this feature is built on.
    # ⚠ was a regex over the route's source, which `lambda: fetch(True)` and
    # `fetch(max_age=0.0)` both walked straight past. Run the route and watch
    # the call instead (redteam round 9).
    def _the_modal_route_shares_the_cache():
        import asyncio
        from orgtree import api as _apimod
        seen = []
        real = limits.fetch

        def _spy(*a, **k):
            seen.append((a, k))
            return {"available": True, "limits": [], "plan": "x"}
        limits.fetch = _spy
        try:
            asyncio.run(_apimod.claude_usage())
        finally:
            limits.fetch = real
        assert seen, "the route no longer goes through limits.fetch"
        # ⚠ and it must still hand the SYNCHRONOUS fetch to a threadpool. The
        # regex this probe replaced happened to cover that; the probe did not,
        # and a direct `await`-less call blocks the whole event loop for up to
        # FETCH_TIMEOUT + a 30 s token refresh — including the bridge
        # `/anthropic` passthrough every sandboxed turn rides (redteam 10).
        j = _api.find("async def claude_usage(")
        fixture(j > 0, "the usage route moved — re-read this check")
        assert "run_in_threadpool(limits.fetch" in _api[j:j + 700], (
            "the route must not call the blocking fetch on the event loop")
        a, k = seen[0]
        assert not a and not k, (
            "the modal polls: forcing a fetch per request (%r/%r) defeats the "
            "single cache the freeze path reads and hammers a "
            "semi-documented endpoint" % (a, k))
    check("usage · the modal route shares the cache rather than forcing it "
          "(runtime)", _the_modal_route_shares_the_cache)

    # ⑦b the GLOW route is cache-only. The header polls it whether or not the
    # modal was ever opened, so a version of it that could fetch would turn an
    # always-on indicator into a standing request against a semi-documented
    # endpoint — the one thing ⑦ exists to prevent, arriving by a second door.
    def _the_glow_route_never_fetches():
        from orgtree import api as _apimod
        real = limits.fetch
        seen = []

        def _spy(*a, **k):
            seen.append((a, k))
            return {"available": True, "limits": [], "plan": "x"}
        saved = dict(limits._cache)
        limits.fetch = _spy
        try:
            limits._cache.update(at=time.time(), data={
                "available": True, "plan": "max",
                "limits": [{"kind": "session", "group": "g", "percent": 91.0,
                            "severity": "warning", "resets_at": None,
                            "is_active": True, "model": None}]})
            hot = _apimod.claude_usage_peek()
            assert not seen, "the glow route fetched (%r)" % (seen,)
            assert hot["available"] and hot["limits"], hot
            # …and a readout too old to be a claim about NOW reports
            # unavailable rather than glowing off a number from last hour
            limits._cache.update(at=time.time() - limits.MAX_EVIDENCE_AGE - 1)
            cold = _apimod.claude_usage_peek()
            assert not cold["available"], cold
            assert not seen, "aging the cache made it fetch (%r)" % (seen,)
        finally:
            limits.fetch = real
            limits._cache.update(saved)
    check("usage · the glow route reads the cache and NEVER fetches, and an "
          "over-age readout stops being a glow", _the_glow_route_never_fetches)

    # ⑧ the identity conjunct in the provenance test: a turn that promoted an
    # agent sentence AND then exited non-zero is judged on the CLI's stderr.
    def _provenance_is_a_conjunction():
        j = _code.find("_trusted_blob = ")
        fixture(j > 0, "the provenance line moved — re-read this check")
        seg = _code[j:j + 200]
        assert "agent_authored" in seg and "err_blob is synth_limit_txt" in seg, (
            "both halves are needed: the flag says an agent sentence was "
            "promoted, the identity says it is what actually froze the node")
    check("trust · provenance is flag AND identity (structural — reaching it "
          "needs a promotion followed by a non-zero exit)",
          _provenance_is_a_conjunction)

    # ⑨ the fuzzy proxied matcher, which exists because an exact-match copy
    # once disagreed with the sandbox about the same string.
    _envkey2 = os.environ.get("ORGTREE_SANDBOX_API_KEY")
    os.environ["ORGTREE_SANDBOX_API_KEY"] = "proxy"
    try:
        check("lane · `ORGTREE_SANDBOX_API_KEY=proxy` reads as PROXIED on "
              "both sides — an exact-match copy read it as a key here and as "
              "proxied in the sandbox", lambda: (
            None if not supervisor.bills_the_key(
                _FakeOrg(slug="zz", kiosk={"sandbox": True}), False)
            else (_ for _ in ()).throw(AssertionError("matchers diverged"))))
    finally:
        if _envkey2 is None:
            os.environ.pop("ORGTREE_SANDBOX_API_KEY", None)
        else:
            os.environ["ORGTREE_SANDBOX_API_KEY"] = _envkey2

    # ⑩ the detector's "short standalone text" half.
    check("detect · a long answer that happens to discuss a usage limit is "
          "not a limit card", lambda: (
        None if not supervisor._result_names_a_limit(
            "Here is what I found. " * 12
            + "The usage limit resets at 9am, per the docs.")
        else (_ for _ in ()).throw(AssertionError("froze an essay"))))

    # ⑪ stale-on-error: a blip must not cost the modal its bars.
    limits.invalidate()
    limits._cache.update(at=time.time(), data={"available": True, "plan": "x",
                                               "limits": []})
    _oa, _ot = limits.subproxy.available, limits.subproxy.get_access_token
    _oo = limits.urllib.request.urlopen
    limits.subproxy.available = lambda: True
    limits.subproxy.get_access_token = lambda: "tok"

    def _boom_open(*a, **k):
        raise OSError("upstream down")
    limits.urllib.request.urlopen = _boom_open
    try:
        check("stale · an upstream blip serves the last good bars instead of "
              "an error box", lambda: (
            None if limits.fetch(force=True).get("available") is True
            else (_ for _ in ()).throw(AssertionError(
                limits.fetch(force=True)))))
    finally:
        limits.subproxy.available, limits.subproxy.get_access_token = _oa, _ot
        limits.urllib.request.urlopen = _oo
        limits.invalidate()

    # ---- R10 · four well-tested functions reached by untested WIRES -------
    # Each of these mutations passed all 905 checks: the function behind the
    # wire is pinned, the line that calls it was not. Same shape every time.

    def _freeze_block():
        j = _code.find("fz[\"limit\"] = True")
        fixture(j > 0, "the freeze site moved — re-read this check")
        return _code[j:_code.find("store.save_org(o2)", j)]

    def _the_window_goes_through_the_bounder():
        seg = _freeze_block()
        i = seg.find("_stamped_win = ")
        fixture(i > 0, "the window write moved — re-read this check")
        rhs = " ".join(seg[i:seg.find("o2.d[", i)].split())
        assert "_fallback_window_until(" in rhs and "trusted=" in rhs, (
            "the ONE line that writes api_fallback_until must route through "
            "the bounder and tell it the provenance — raw, a probe freeze "
            "opens 300 s (under the documented floor) and an 8-day epoch "
            "opens 8 days (over the ceiling): %s" % rhs)
    check("money · the window write is bounded and provenance-aware "
          "(structural — the wire, not the arithmetic)",
          _the_window_goes_through_the_bounder)

    def _the_stamp_is_told_the_billing_lane():
        seg = _freeze_block()
        i = seg.find("_rts, _rsrc = _limit_reset_ts(")
        fixture(i > 0, "the stamp call moved — re-read this check")
        rhs = " ".join(seg[i:seg.find(")", seg.find("trusted=", i))].split())
        assert "subscription=not _billed_key" in rhs, (
            "D-133 §WHOSE QUOTA is decided here: told `subscription=True` a "
            "key-billed org has its freeze timed off the HOST's lanes, and "
            "the correction pass — still told the truth — declines to "
            "overwrite it: %s" % rhs)
    check("lane · the stamp is told the billing lane (structural)",
          _the_stamp_is_told_the_billing_lane)

    def _the_fable_lock_goes_through_its_own_clock():
        seg = _freeze_block()
        i = seg.find("fable_limit_hit(")
        fixture(i > 0, "the escalation moved — re-read this check")
        rhs = " ".join(seg[i:i + 260].split())
        assert "_fable_lock_ts(" in rhs, (
            "FABLE-2 verbatim: `fz[\"until_ts\"]` may be the 5-minute probe "
            "floor, so passing it here self-releases a week-long org-wide "
            "lock ~288 times a day: %s" % rhs)
    check("fable · the org-wide lock is timed by its own clock, never by the "
          "node's freeze (structural)", _the_fable_lock_goes_through_its_own_clock)

    # ⑤ the auto_resume toggle, BEHAVIOURALLY. The existing guard greps the
    # filter's contents, so it stays green when the whole branch goes dead —
    # the same self-certification family as the PROBE_FLOOR substring guard.
    _tog = _FakeOrg(slug="zz")
    _tog.nodes = {"n": {"state": "live",
                        "frozen": {"limit": True, "until_ts": now - 120}}}
    check("wake · a limit freeze is ready only when its own reset has passed "
          "— the timer, not the toggle, owns that", lambda: (
        None if "n" in supervisor.auto_resume_ready(_tog, now)
        else (_ for _ in ()).throw(AssertionError("a passed reset is ready"))))

    def _the_toggle_gates_the_limit_kind():
        j = _code.find("def start_auto_resume_loop(")
        fixture(j > 0, "the resume loop moved — re-read this check")
        body = _code[j:_code.find("\ndef ", j + 1)]
        k = body.find('org.d.get("auto_resume")')
        fixture(k > 0, "the toggle read moved — re-read this check")
        seg = " ".join(body[max(0, k - 120):k + 60].split())
        assert "if not" in seg, (
            "the toggle must still GATE something — a dead branch makes every "
            "limit-frozen node auto-wake and spend against the quota the user "
            "opted out of: %s" % seg)
    check("wake · the auto_resume toggle still gates the limit kind "
          "(structural — a dead branch is invisible to a contents grep)",
          _the_toggle_gates_the_limit_kind)

    # ⑥ the 429 markers the first `is_rate_limit` missed — one hyphen was the
    # difference between a 15-minute window and a six-day one
    for _b, _want in (
            ("API Error: 429 usage limit reached", True),
            ("…exceeded your weekly rate-limit…", True),
            ("…exceeded your weekly rate_limit…", True),
            ("anthropic.RateLimitError: too many requests", True),
            ("per-minute rate limit exceeded", True),
            ("Claude AI usage limit reached", False),
            ("your corporate limit applies", False),
            ("You've reached your Fable 5 limit.", False)):
        check("rate-limit · %r → %s" % (_b[:44], _want), (
            lambda b=_b, w=_want: (
                None if limits.is_rate_limit(b) is w
                else (_ for _ in ()).throw(AssertionError(b)))))

    # ⑦ a `critical` lane must actually reach the tightest warm band
    check("warm · a critical lane reaches the 45 s band — pressure() floors "
          "it at exactly 95.0, so a strict `> 95` could never see the one "
          "signal the band exists for", lambda: (
        None if (_readout(("session", "session", 3, "critical", 3600, True,
                           None)) or supervisor._warm_interval(
                               limits.pressure()) == 45)
        else (_ for _ in ()).throw(AssertionError(limits.pressure()))))
    limits.invalidate()

    # W3 · the correction pass must tighten the cache window, or it re-reads
    # the very entry the freeze stamped from
    check("re-read · the correction's max_age is tighter than the ordinary "
          "cache TTL", lambda: (
        None if 0 < limits.REREAD_MAX_AGE < limits.CACHE_TTL
        else (_ for _ in ()).throw(AssertionError(limits.REREAD_MAX_AGE))))

    # E1 · `_plan` is inside fetch's "never raises" contract
    _credbak = limits.subproxy.CREDS
    _bad = os.path.join(_TMP, "badcreds.json")
    with open(_bad, "w", encoding="utf-8") as _f:
        _f.write('{"claudeAiOauth": ["not", "a", "dict"]}')
    limits.subproxy.CREDS = _bad
    try:
        check("reshape · a malformed credentials file degrades the plan "
              "string, it does not raise out of fetch", lambda: (
            None if limits._plan() == ""
            else (_ for _ in ()).throw(AssertionError(limits._plan()))))
    finally:
        limits.subproxy.CREDS = _credbak

    # ---- R9 · a second, independent ~100-mutation campaign ----------------

    # S28 · the money boundary D-130 writes down and nothing tested: a FABLE
    # TIER quota is fable_limit_policy's lane, not a billing lane. Dropping
    # `not _fable_tier` from the window `elif` opens up to 7 days of org-wide
    # metered billing on it.
    def _a_fable_tier_quota_opens_no_window():
        j = _code.find("fz[\"limit\"] = True")
        fixture(j > 0, "the freeze site moved — re-read this check")
        seg = _code[j:_code.find("store.save_org(o2)", j)]
        i = seg.find('o2.d["api_fallback_until"] = _stamped_win')
        fixture(i > 0, "the window write moved — re-read this check")
        cond = seg[seg.rfind("elif", 0, i):i]
        assert "_fable_tier" in cond and "_trusted_blob" in cond, (
            "the window must not open on a fable TIER quota (D-130: that lane "
            "belongs to fable_limit_policy, not to billing) nor on untrusted "
            "evidence — condition was: %s" % " ".join(cond.split()))
    check("money · a fable-tier quota and untrusted text both open NO "
          "key-billing window (structural — the tier path needs a real fable "
          "node and a policy)", _a_fable_tier_quota_opens_no_window)

    # F7 · the correction pass has the FINAL say on both the stamp and the
    # window, so it must be told the same lane the freeze was.
    def _the_correction_pass_is_told_the_lane():
        j = _code.find("_spawn_reset_refresh(slug, nid, err_blob")
        fixture(j > 0, "the spawn call moved — re-read this check")
        seg = " ".join(_code[j:j + 260].split())
        assert "not _billed_key" in seg and "_trusted_blob" in seg, (
            "the pass rewrites until_ts AND api_fallback_until; told "
            "`subscription=True` for a key-billed freeze it re-introduces the "
            "whole-quota bug in the one place that has the last word: %s"
            % seg)
    check("lane · the correction pass inherits the freeze's lane and trust "
          "(structural)", _the_correction_pass_is_told_the_lane)

    # T5 · …and `_billed_key` itself is the spawn capture, not a re-read.
    def _the_billing_lane_is_the_spawn_capture():
        f = _code.find("fz[\"limit\"] = True")
        fixture(f > 0, "the freeze site moved — re-read this check")
        j = _code.rfind("_billed_key = ", 0, f)
        fixture(j > 0, "the lane capture moved — re-read this check")
        rhs = _code[j + len("_billed_key = "):_code.find("\n", j)].strip()
        assert rhs == "billed_key", (
            "combining a spawn-captured window with org fields re-read at "
            "freeze time is the bug the comment names — a mid-turn settings "
            "change then re-labels a key-billed turn: %r" % rhs)
    check("lane · the freeze reads the spawn-captured billing lane "
          "(structural — a mid-turn settings change is not drivable)",
          _the_billing_lane_is_the_spawn_capture)

    # F3 · the correction's no-op threshold. Both existing checks straddle it
    # (a 30 s move and a 3.9 h move); neither pins the boundary, so widening
    # it to 100 minutes let an over-long stamp AND window survive correction.
    _slug9, _nid9 = probe_org()

    def _stamp9(win):
        o = store.load_org(_slug9)
        o.nodes[_nid9]["frozen"] = {"at": "x", "limit": True,
                                    "until_ts": now + 300, "until": "x",
                                    "reset_src": "probe"}
        o.d["api_key"] = "sk-test"
        o.d["api_fallback"] = True
        o.d["api_fallback_until"] = win
        store.save_org(o)

    _real9 = supervisor._limit_reset_ts
    try:
        _w9 = supervisor._fallback_window_until(now + 300, now)
        for _delta, _want, _why in ((45, False, "a move under a minute is "
                                                "not worth a write"),
                                    (120, True, "a two-minute move IS")):
            supervisor._limit_reset_ts = (
                lambda blob, _d=_delta, **kw: (now + 300 + _d, "usage:session"))
            _stamp9(_w9)
            _got = supervisor._refresh_freeze_reset(
                _slug9, _nid9, "limit", now + 300, _w9)
            check("refresh · %s (the 60 s threshold, pinned at the boundary)"
                  % _why, (
                lambda g=_got, w=_want: (
                    None if g is w
                    else (_ for _ in ()).throw(AssertionError(
                        "wrote=%s wanted=%s" % (g, w))))))
    finally:
        supervisor._limit_reset_ts = _real9

    # L2/L1/L28 · `_iso_to_epoch`. The helper the readout checks use only ever
    # emits `+00:00`, so the `Z` branch — the one this host's 3.10 needs — was
    # never exercised: dropping it makes EVERY readout return None and the
    # whole lookup degrade to the blind probe floor.
    _epoch_cases = (("2026-08-18T15:30:00Z", "the Z suffix 3.10 cannot parse"),
                    ("2026-08-18T15:30:00+00:00", "an explicit offset"),
                    ("2026-08-18T15:30:00", "a naive reading (UTC upstream)"))
    _vals = [limits._iso_to_epoch(t) for t, _ in _epoch_cases]
    check("iso · every shape the upstream emits parses, and to the SAME "
          "instant (%s)" % "; ".join(w for _, w in _epoch_cases), lambda: (
        None if all(v is not None for v in _vals)
        and max(_vals) - min(_vals) < 1
        else (_ for _ in ()).throw(AssertionError(list(zip(_epoch_cases,
                                                           _vals))))))

    # S3 · the past-floor on a parsed reset. The existing check uses a
    # timestamp over a year old, so widening the floor 1440x was invisible.
    check("band · a reset five minutes in the past is not a horizon — it "
          "would make the node 'ready' on every 30 s tick", lambda: (
        None if supervisor._parse_limit_reset_ts(
            "usage limit reached|%d" % int(now - 300), None, now=now) is None
        and supervisor._parse_limit_reset_ts(
            "usage limit reached|%d" % int(now + 300), None, now=now)
        is not None
        else (_ for _ in ()).throw(AssertionError("past floor widened"))))

    # R11/R12 · the toggle-OFF filter. D-122 governs the connection kind; a
    # record carrying BOTH kinds waits on the toggle, and a key-lane freeze
    # must not be woken by it either.
    def _the_toggle_off_filter_is_narrow():
        j = _code.find("def start_auto_resume_loop(")
        fixture(j > 0, "the resume loop moved — re-read this check")
        body = _code[j:_code.find("\ndef ", j + 1)]
        k = body.find("_resumable(org.node(nid))")
        fixture(k > 0, "the toggle-off filter moved — re-read this check")
        seg = " ".join(body[k:k + 400].split())
        assert 'fz.get("connection") and not fz.get("limit")' in seg, (
            "a record carrying BOTH kinds must wait on the toggle (D-122)")
        assert 'not fz.get("on_fallback")' in seg, (
            "a key-lane freeze must not be woken by the fallback clause")
    check("wake · the toggle-off filter admits only PURE connection freezes "
          "and subscription-lane limits (structural)",
          _the_toggle_off_filter_is_narrow)

    # W2 · the warm loop's org gate — 1920 requests/day on an org-less install
    def _the_warm_loop_is_gated():
        j = _code.find("def start_usage_warm_loop(")
        fixture(j > 0, "the warm loop moved — re-read this check")
        seg = _code[j:_code.find("\ndef ", j + 1)]
        assert "limits.available()" in seg and "store.list_orgs()" in seg, (
            "the loop must be silent with no credentials AND with no orgs")
    check("warm · the loop is gated on credentials and on there being an org "
          "to warm the cache for (structural)", _the_warm_loop_is_gated)

    # L18 · a scoped lane answers only for ITS model
    _readout(("weekly_scoped", "weekly", 90, "normal", 2 * 3600, True, "Opus"),
             ("weekly_scoped", "weekly", 30, "normal", 5 * 86400, False,
              "Fable"))
    check("lane · a model-named limit is answered by THAT model's pool, not "
          "whichever scoped lane comes first", lambda: (
        None if abs(limits.reset_for(
            "You've reached your Fable 5 limit")[0] - (now + 5 * 86400)) < 60
        else (_ for _ in ()).throw(AssertionError(
            limits.reset_for("You've reached your Fable 5 limit")))))
    limits.invalidate()

    # X4 · the container LABEL must read `proxied` the same fuzzy way the
    # sandbox does — the mirror of the bills_the_key matcher bug
    _envkey3 = os.environ.get("ORGTREE_SANDBOX_API_KEY")
    os.environ["ORGTREE_SANDBOX_API_KEY"] = "proxy"
    try:
        check("sandbox · the auth LABEL agrees with the runtime auth on "
              "`=proxy` — a mismatch labels a proxied container `key:<hash>` "
              "and recreates it forever", lambda: (
            None if sbx.auth_label(_FakeOrg(slug="zz",
                                            kiosk={"sandbox": True})) == "proxy"
            else (_ for _ in ()).throw(AssertionError(
                sbx.auth_label(_FakeOrg(slug="zz",
                                        kiosk={"sandbox": True}))))))
    finally:
        if _envkey3 is None:
            os.environ.pop("ORGTREE_SANDBOX_API_KEY", None)
        else:
            os.environ["ORGTREE_SANDBOX_API_KEY"] = _envkey3

    # B-1 · a per-minute RATE limit is not a usage LANE
    _readout(("session", "session", 80, "normal", 4 * 3600, True, None))
    check("rate-limit · a 429 'per-minute rate limit' is not answered from "
          "the subscription's 5-hour lane — that parked a node for four "
          "hours, and billed a fallback org's key for four hours, against a "
          "wall that lifts in a minute", lambda: (
        None if supervisor._limit_reset_ts(
            "API Error: 429 rate_limit_error: Number of request tokens has "
            "exceeded your per-minute rate limit") == (None, "")
        else (_ for _ in ()).throw(AssertionError(
            supervisor._limit_reset_ts(
                "API Error: 429 rate_limit_error: per-minute rate limit")))))
    check("rate-limit · …but its own prose still answers, and an ordinary "
          "usage limit is unaffected", lambda: (
        None if supervisor._limit_reset_ts(
            "rate limit exceeded, try again in 2 minutes")[1] == "text"
        and supervisor._limit_reset_ts(
            "Claude AI usage limit reached")[1] == "usage:session"
        else (_ for _ in ()).throw(AssertionError("over-tightened"))))
    limits.invalidate()

    # ---- R7 · the gaps mutation testing found in the checks above ---------
    # Each of these was green under a mutation that broke the thing it names.

    # ① `_normalize`'s OUTPUT CONTRACT, end to end. Every readout check above
    # hand-builds the POST-normalize shape, so renaming an emitted key stayed
    # green while (a) every readout-sourced freeze silently fell to the blind
    # probe floor and (b) the modal's reset column went blank — this is the
    # seam between the two consumers the feature deliberately merged onto one
    # cache, so it is pinned against a RAW upstream payload.
    _raw = {"limits": [
        {"kind": "session", "group": "session", "percent": 92,
         "severity": "critical", "resets_at": _iso(2 * 3600),
         "is_active": True, "scope": None},
        {"kind": "weekly_scoped", "group": "weekly", "percent": 40,
         "severity": "normal", "resets_at": _iso(5 * 86400),
         "is_active": False,
         "scope": {"model": {"display_name": "Fable"}}}]}
    _norm = limits._normalize(_raw)
    check("contract · _normalize emits exactly the keys both consumers read "
          "(reset_for's `kind`/`resets_at`/`is_active`, the modal's "
          "`percent`/`severity`/`model`/`group`)", lambda: (
        None if all(set(x) == {"kind", "group", "percent", "severity",
                               "resets_at", "is_active", "model"}
                    for x in _norm)
        and _norm[1]["model"] == "Fable" and _norm[0]["is_active"] is True
        else (_ for _ in ()).throw(AssertionError(_norm))))
    limits._cache.update(at=time.time(), data={"available": True, "plan": "max",
                                               "limits": _norm})
    check("contract · …and a RAW payload, normalized, actually answers a "
          "freeze", lambda: (
        None if limits.reset_for("Claude AI usage limit reached")[1]
        == "usage:session"
        else (_ for _ in ()).throw(AssertionError(
            limits.reset_for("Claude AI usage limit reached")))))
    limits.invalidate()

    # ② the money constants themselves. The checks above compare against the
    # constants, so raising the ceiling 7 d → 60 d stayed green. Changing
    # these is a deliberate act; make it a loud one.
    check("constants · the key-billing window is 15 min … 7 d + 1 h and the "
          "readout stops being evidence at 15 min", lambda: (
        None if (supervisor.FALLBACK_MIN_WINDOW == 900.0
                 and supervisor.FALLBACK_MAX_WINDOW == 7 * 86400.0 + 3600.0
                 and limits.MAX_EVIDENCE_AGE == 900.0
                 and limits.LANE_SECONDS["session"] == 18000.0)
        else (_ for _ in ()).throw(AssertionError(
            (supervisor.FALLBACK_MIN_WINDOW, supervisor.FALLBACK_MAX_WINDOW,
             limits.MAX_EVIDENCE_AGE)))))

    # ③/④ WHICH self-report caps the node, and what the record then says.
    # ⚠ Driven through REAL turns. The first cut of this check re-implemented
    # the counting rule inside the test and would have passed against any
    # production code at all — the very vacuity these rounds keep finding.
    if shutil.which("node"):
        _cs, _cn = probe_org()
        set_mode("plain", reply="Usage limit reached. Try again in 1 minute.")
        _seq = []
        for _i in range(supervisor.UNTRUSTED_LIMIT_RUNS + 1):
            _o = store.load_org(_cs)
            _o.nodes[_cn].pop("frozen", None)     # a wake, without a turn
            store.save_org(_o)
            run_turn(_cs, _cn)
            _fz = store.load_org(_cs).nodes[_cn].get("frozen") or {}
            _seq.append((_fz.get("until_ts"), _fz.get("reset_src")))
        _capped_at = next((i + 1 for i, (t, _) in enumerate(_seq)
                           if t is None), None)
        check("cap · the Nth self-report caps, not the N+1th (N = "
              "UNTRUSTED_LIMIT_RUNS = %d) — an off-by-one here grants a free "
              "turn every run" % supervisor.UNTRUSTED_LIMIT_RUNS, lambda: (
            None if _capped_at == supervisor.UNTRUSTED_LIMIT_RUNS
            else (_ for _ in ()).throw(AssertionError(
                "capped at %s: %s" % (_capped_at, _seq)))))
        check("cap · …and the capped record stops claiming a provenance for "
              "the timestamp it just deleted", lambda: (
            None if _seq[supervisor.UNTRUSTED_LIMIT_RUNS - 1][1] == "capped"
            and _seq[0][1] == "text"
            else (_ for _ in ()).throw(AssertionError(_seq))))

    # ⑤ the freeze site's USE of `_sane_inherited`. The helper has unit
    # checks; the call site had none, and no test can drive it — a frozen
    # node runs no turns, so a surviving `until_ts` cannot be reached from
    # outside. Structural, therefore, and honest about why.
    def _the_inherited_timestamp_is_banded_at_the_call_site():
        # anchored on the FREEZE SITE, not the first stamping line in the
        # file — that one belongs to the correction pass
        j = _code.find("fz[\"limit\"] = True")
        fixture(j > 0, "the freeze site moved — re-read this check")
        i = _code.find("fz[\"until_ts\"] = ", j)
        fixture(i > j, "the stamping line moved — re-read this check")
        assert "_sane_inherited(" in _code[i:i + 160], (
            "an inherited until_ts is the one number in this path that no "
            "band has seen, and on the trusted branch it prices the "
            "api_fallback window — clamped only by the 7 d ceiling")
    check("inherit · the call site bands what it inherits (structural — the "
          "path is unreachable from a test, a frozen node runs no turns)",
          _the_inherited_timestamp_is_banded_at_the_call_site)

    # ---- single-flight: a limit STORM costs one request, not N ------------
    # Nothing pinned this (the property arrived as redteam round 2 item 7 and
    # only ever had a code review behind it). N nodes freezing together each
    # spawn a correction thread; without the double-checked `_fetch_lock` they
    # are N concurrent GETs at a semi-documented endpoint, each serializing
    # behind subproxy's token lock, and a 429 from that herd puts every one of
    # them on the stale path.
    limits.invalidate()
    _hits = []
    _orig_avail, _orig_tok = limits.subproxy.available, limits.subproxy.get_access_token
    _orig_open = limits.urllib.request.urlopen

    class _Slow:
        def __enter__(self):
            _hits.append(1)
            time.sleep(0.25)          # long enough for the herd to pile up
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"limits": []}'

    limits.subproxy.available = lambda: True
    limits.subproxy.get_access_token = lambda: "tok"
    limits.urllib.request.urlopen = lambda *a, **k: _Slow()
    _orig_load = limits.json.load
    limits.json.load = lambda fp: {"limits": []}
    try:
        _threads = [threading.Thread(target=limits.fetch) for _ in range(8)]
        for t in _threads:
            t.start()
        for t in _threads:
            t.join(5)
        check("storm · eight simultaneous freezes cost ONE upstream request, "
              "not eight", lambda: (
            None if len(_hits) == 1
            else (_ for _ in ()).throw(AssertionError("%d requests" % len(_hits)))))
        check("storm · …and every one of them got the answer", lambda: (
            None if limits.cached() is not None
            and bool(limits.cached().get("available"))
            else (_ for _ in ()).throw(AssertionError(limits.cached()))))
    finally:
        limits.subproxy.available = _orig_avail
        limits.subproxy.get_access_token = _orig_tok
        limits.urllib.request.urlopen = _orig_open
        limits.json.load = _orig_load
        limits.invalidate()

    # ---- R3·F6 `fetch` promises never to raise ----------------------------
    for _shape in ({"limits": 3}, {"limits": "xx"}, {"limits": [None, 3]},
                   {"limits": [{"scope": {"model": "str"}}]}, {"five_hour": 7},
                   []):
        check("reshape · a %r payload degrades, it does not raise"
              % (_shape if not isinstance(_shape, dict)
                 else list(_shape)[:1] or "empty"), (
            lambda sh=_shape: (
                None if isinstance(limits._normalize(
                    sh if isinstance(sh, dict) else {}), list)
                else (_ for _ in ()).throw(AssertionError(sh)))))

    # ---- R4·F3 an agent may not halt (or dissolve) the org -----------------
    if shutil.which("node"):
        _fs, _fn = probe_org()
        _fo = store.load_org(_fs)
        _fo.nodes[_fn]["model"] = "fable"
        store.save_org(_fo)
        set_mode("plain",
                 reply="I've reached the Fable limit - try again in 3 hours.")
        run_turn(_fs, _fn)
        check("fable · an AGENT'S OWN SENTENCE does not fire the org-wide "
              "escalation — under the dissolve policy that trigger archives "
              "every fable node in the org", lambda: (
            None if not store.load_org(_fs).d.get("fable_lock")
            else (_ for _ in ()).throw(AssertionError(
                store.load_org(_fs).d.get("fable_lock")))))
        _fo = store.load_org(_fs)
        check("fable · …and the node itself still froze, with a timestamp "
              "(the cheap half stays)", lambda: (
            None if (_fo.nodes[_fn].get("frozen") or {}).get("until_ts")
            else (_ for _ in ()).throw(AssertionError(
                _fo.nodes[_fn].get("frozen")))))

    # ---- R5·F1 an agent cannot put the ORG on the key, at any rate ---------
    # Flooring an unvouched window at 15 minutes bounded ONE incident and not
    # the RATE: the window makes the node immediately resumable (even with
    # auto_resume off), the resume replays the same prompt, and the same
    # sentence re-opens it — measured at 95% duty, indefinitely, with the
    # whole org on the user's metered key (redteam 2026-08-18).
    if shutil.which("node"):
        _us, _un = probe_org()
        _uo = store.load_org(_us)
        _uo.d["api_key"] = "sk-test"
        _uo.d["api_fallback"] = True
        _uo.d["auto_resume"] = True
        store.save_org(_uo)
        set_mode("plain",
                 reply="Usage limit reached. Try again in 1 minute.")
        run_turn(_us, _un)
        _after = store.load_org(_us)
    if shutil.which("node"):
        _s3, _un2 = probe_org()
        set_mode("plain", reply=(
            "Usage limit reached for this account, resets once you re-auth "
            "at http://corp-sso-refresh/login — try again in 1 minute"))
        run_turn(_s3, _un2)
        _after2 = store.load_org(_s3)
        check("chrome · the agent's own phrasing never becomes the freeze "
              "LABEL — `until` is projected by ledger.tree() and rendered as "
              "system chrome in the org header and the node badge, kiosk "
              "visitors included", lambda: (
            None if "corp-sso" not in str(
                (_after2.nodes[_un2].get("frozen") or {}).get("until") or "")
            and (_after2.nodes[_un2].get("frozen") or {}).get("until")
            else (_ for _ in ()).throw(AssertionError(
                (_after2.nodes[_un2].get("frozen") or {}).get("until")))))

        check("rate · a self-reported limit freezes the node but opens NO "
              "key window — a real wall is always reported BY the CLI, so "
              "declining costs a genuine limit nothing", lambda: (
            None if (_after.nodes[_un].get("frozen") or {}).get("limit")
            and not _after.d.get("api_fallback_until")
            else (_ for _ in ()).throw(AssertionError(
                "window=%s frozen=%s" % (_after.d.get("api_fallback_until"),
                                         _after.nodes[_un].get("frozen"))))))
        check("rate · …and the freeze is marked untrusted, so a reader can "
              "tell a floored window from a priced one", lambda: (
            None if (_after.nodes[_un].get("frozen") or {}).get("untrusted")
            and _after.nodes[_un].get("untrusted_limit_run") == 1
            else (_ for _ in ()).throw(AssertionError(
                _after.nodes[_un].get("frozen")))))

        # …and the run counter ends the loop rather than riding it
        for _i in range(supervisor.UNTRUSTED_LIMIT_RUNS):
            _o = store.load_org(_us)
            _o.nodes[_un].pop("frozen", None)
            store.save_org(_o)
            run_turn(_us, _un)
        _last = store.load_org(_us)
        # ⚠ BOTH halves, because the first attempt at this passed for the
        # wrong reason: `untrusted: True` tripped `_resumable`'s
        # unknown-True-key refusal, which suppressed the timer AND ▶ — the
        # node could never be woken by anyone, the pre-№41 spend-freeze trap
        # in a new costume (self-caught 2026-08-18).
        check("rate · a capped untrusted freeze is still RESUMABLE — the cap "
              "silences the timer, not the person", lambda: (
            None if supervisor._resumable(_last.nodes[_un]) is not None
            else (_ for _ in ()).throw(AssertionError(
                "▶ would skip this node forever"))))
        check("rate · …and an open window from another node's REAL limit "
              "does not drag it along either", lambda: (
            None if _un not in supervisor.auto_resume_ready(
                _with_window(_us, _un))
            else (_ for _ in ()).throw(AssertionError("fast-wake dragged it"))))
        check("rate · after %d consecutive self-reported limits the node "
              "stops waking itself and waits for a person"
              % supervisor.UNTRUSTED_LIMIT_RUNS, lambda: (
            None if (_last.nodes[_un]["frozen"].get("until_ts") is None
                     and _un not in supervisor.auto_resume_ready(_last))
            else (_ for _ in ()).throw(AssertionError(
                "%s ready=%s" % (_last.nodes[_un]["frozen"],
                                 supervisor.auto_resume_ready(_last))))))
        _o = store.load_org(_us)
        _o.nodes[_un].pop("frozen", None)
        store.save_org(_o)
        set_mode("plain", reply="all done, nothing to report")
        run_turn(_us, _un)
        check("rate · a completed turn clears the run, like the connection "
              "kind's — the count is CONSECUTIVE",
              lambda: (
            None if not store.load_org(_us).nodes[_un].get(
                "untrusted_limit_run")
            else (_ for _ in ()).throw(AssertionError(
                store.load_org(_us).nodes[_un].get("untrusted_limit_run")))))

    # ---- R2·F8 an unknown lane kind is short, not long ---------------------
    check("lane · an upstream lane this build has never heard of takes the "
          "SHORTEST band, not the longest", lambda: (
        None if limits.lane_horizon("monthly") == limits.lane_horizon("session")
        else (_ for _ in ()).throw(AssertionError(
            limits.lane_horizon("monthly")))))

    # ---- R2·F2 the epoch exemption is about PROVENANCE ---------------------
    # ⚠ through `_limit_reset_ts`, the seam the freeze site actually calls.
    # Testing `_parse_limit_reset_ts` directly omitted the `kind` argument the
    # real path always supplies — and the whole defect was that the untrusted
    # blob got to CHOOSE that kind, so the check was green while the code was
    # wrong (redteam 2026-08-18).
    #
    # The board is deliberately weekly-only: every route an untrusted blob
    # could take to a 7-day answer (its own epoch, its own "try again in N",
    # or the readout's genuine weekly lane) has to come back empty, while the
    # same wording from the CLI still resolves.
    _readout(("weekly_all", "weekly", 88, "normal", 6 * 86400, False, None))
    _agent_epoch = "Weekly usage limit reached|%d" % (int(now) + 7 * 86400)
    _agent_rel = "Your weekly usage limit was reached. Try again in 168 hours."
    check("trust · an epoch the CLI reported is taken at face value", lambda: (
        None if supervisor._limit_reset_ts(_agent_epoch)[1] == "text"
        else (_ for _ in ()).throw(AssertionError("refused the CLI"))))
    check("trust · the same 38-character text written by the AGENT is refused "
          "— otherwise a node opens a week-long key-billing window against a "
          "wall that never existed, by typing one line", lambda: (
        None if supervisor._limit_reset_ts(_agent_epoch, trusted=False)
        == (None, "")
        else (_ for _ in ()).throw(AssertionError(
            supervisor._limit_reset_ts(_agent_epoch, trusted=False)))))
    check("trust · …nor by naming the weekly lane in a relative form",
          lambda: (
        None if supervisor._limit_reset_ts(_agent_rel, trusted=False)
        == (None, "")
        else (_ for _ in ()).throw(AssertionError(
            supervisor._limit_reset_ts(_agent_rel, trusted=False)))))
    check("trust · …nor through the READOUT, where an unvouched blob is "
          "unnamed too and the weekly lane sits outside the session cap",
          lambda: (
        None if supervisor._limit_reset_ts(
            "weekly usage limit reached", trusted=False) == (None, "")
        else (_ for _ in ()).throw(AssertionError(
            supervisor._limit_reset_ts("weekly usage limit reached",
                                       trusted=False)))))
    # ⚠ the four checks above pin the SEVEN-DAY case. The band an untrusted
    # blob falls back to is the SESSION lane, so an epoch five hours out was
    # accepted and priced a five-hour, org-wide key window against a wall that
    # does not exist — invisible to a test that only measures "not weekly"
    # (redteam 2026-08-18). Pin the money, not the lane.
    _agent_5h = "Claude AI usage limit reached|%d" % (int(now) + 5 * 3600)
    check("trust · an unvouched blob may still WAKE a node on its own "
          "timestamp", lambda: (
        None if supervisor._limit_reset_ts(_agent_5h, trusted=False)[0]
        else (_ for _ in ()).throw(AssertionError("lost the wake time"))))
    check("trust · …but it may not PRICE a window: an agent's sentence must "
          "never move the whole org onto the user's metered key", lambda: (
        None if abs(supervisor._fallback_window_until(
            now + 5 * 3600, now, trusted=False)
            - (now + supervisor.FALLBACK_MIN_WINDOW)) < 1
        and abs(supervisor._fallback_window_until(now + 5 * 3600, now)
                - (now + 5 * 3600)) < 1
        else (_ for _ in ()).throw(AssertionError(
            supervisor._fallback_window_until(now + 5 * 3600, now,
                                              trusted=False) - now))))

    # ── a NAMED lane bands the epoch too ────────────────────────────────────
    check("band · 'your session limit …|<epoch 8 days out>' is two pieces of "
          "evidence contradicting each other — the lane wins", lambda: (
        None if supervisor._parse_limit_reset_ts(
            "you have hit your session limit|%d" % (int(now) + 8 * 86400),
            "session", now=now) is None
        else (_ for _ in ()).throw(AssertionError("epoch beat its own lane"))))
    check("band · …and through the seam the freeze site uses, not just the "
          "parser (the lane has to be DERIVED from that wording)", lambda: (
        None if supervisor._limit_reset_ts(
            "you have hit your session limit|%d" % (int(now) + 8 * 86400))[1]
        != "text"
        else (_ for _ in ()).throw(AssertionError(
            supervisor._limit_reset_ts(
                "you have hit your session limit|%d"
                % (int(now) + 8 * 86400))))))
    check("band · …while an epoch with no lane word beside it is still taken "
          "at face value", lambda: (
        None if supervisor._parse_limit_reset_ts(
            "Claude usage limit reached|%d" % (int(now) + 7 * 86400),
            None, now=now) is not None
        else (_ for _ in ()).throw(AssertionError("refused a bare epoch"))))

    check("trust · a TRUSTED weekly wording still reaches the weekly lane",
          lambda: (
        None if supervisor._limit_reset_ts("weekly usage limit reached")[1]
        == "usage:weekly_all"
        else (_ for _ in ()).throw(AssertionError(
            supervisor._limit_reset_ts("weekly usage limit reached")))))

    # ---- R2·F5 the detector must not inherit the clock's bands -------------
    for _blob, _want in (("Claude usage limit reached. Resets 9am.", True),
                         ("Claude usage limit reached. Try again in 20 hours.",
                          True),
                         ("Claude usage limit reached. Try again in 2 hours.",
                          True),
                         ("the rate limit resets nightly, so I stopped here",
                          False)):
        check("detect · %r freezes=%s" % (_blob[:46], _want), (
            lambda b=_blob, w=_want: (
                None if supervisor._result_names_a_limit(b) == w
                else (_ for _ in ()).throw(AssertionError(b)))))

    # ---- R2·F4 a session-length time may never time a WEEKLY tier lock -----
    check("fable · 'your Fable 5 limit. Try again in 3 hours.' leaves the "
          "org-wide lock no_reset — releasing it 3 h into a week un-halts "
          "every fable node ~56 times over (FABLE-2)", lambda: (
        None if supervisor._fable_lock_ts(
            "You've reached your Fable 5 limit. Try again in 3 hours.",
            None, "", now=now) is None
        else (_ for _ in ()).throw(AssertionError("released early"))))
    check("fable · …and the readout's weekly lane DOES time it (the whole "
          "point: no_reset waits for a human)", lambda: (
        None if supervisor._fable_lock_ts(
            "You've reached your Fable 5 limit.", now + 5 * 86400,
            "usage:weekly_scoped", now=now) == now + 5 * 86400
        else (_ for _ in ()).throw(AssertionError("still no_reset"))))
    check("fable · an AGENT-authored 'your Fable 5 limit. Try again in 100 "
          "hours.' cannot halt the org for four days", lambda: (
        None if supervisor._fable_lock_ts(
            "your Fable 5 limit. Try again in 100 hours.", None, "",
            trusted=False, now=now) is None
        else (_ for _ in ()).throw(AssertionError("agent set the lock"))))
    check("fable · a SESSION-lane readout answer is refused there too",
          lambda: (
        None if supervisor._fable_lock_ts(
            "You've reached your Fable 5 limit.", now + 3600,
            "usage:session", now=now) is None
        else (_ for _ in ()).throw(AssertionError("session lane timed a lock"))))

    # ---- R2·F9 an inherited timestamp is banded like every other -----------
    check("inherit · a re-freeze keeps a plausible old horizon", lambda: (
        None if supervisor._sane_inherited(now + 3600) == now + 3600
        else (_ for _ in ()).throw(AssertionError("dropped a good one"))))
    check("inherit · …and drops one in the past or past the longest lane",
          lambda: (
        None if supervisor._sane_inherited(now - 5) is None
        and supervisor._sane_inherited(now + 90 * 86400) is None
        and supervisor._sane_inherited(None) is None
        else (_ for _ in ()).throw(AssertionError("kept an absurd one"))))

    # ---- whose quota was it? (redteam 2026-08-18) -------------------------
    _readout(("session", "session", 99, "critical", 4 * 3600, True, None))
    check("lane · a turn that billed the ORG'S KEY is not timed off the "
          "host subscription's lanes — a per-minute API rate limit was "
          "parking nodes for four hours", lambda: (
        None if supervisor._limit_reset_ts(
            "API Error: 429 rate_limit_error — Number of request tokens has "
            "exceeded your per-minute rate limit", subscription=False)
        == (None, "")
        else (_ for _ in ()).throw(AssertionError("read someone else's lane"))))
    check("lane · …but the prose in that same error still answers, because "
          "it came from the wall that was actually hit", lambda: (
        None if supervisor._limit_reset_ts(
            "rate limit exceeded, try again in 2 minutes",
            subscription=False)[1] == "text"
        else (_ for _ in ()).throw(AssertionError("dropped the prose"))))

    check("lane · a permanent-key org bills the key on every turn", lambda: (
        None if supervisor.bills_the_key(_FakeOrg(api_key="sk"), False)
        else (_ for _ in ()).throw(AssertionError("read as subscription"))))
    check("lane · a fallback org bills the subscription until its window is "
          "open", lambda: (
        None if not supervisor.bills_the_key(
            _FakeOrg(api_key="sk", api_fallback=True), False)
        and supervisor.bills_the_key(
            _FakeOrg(api_key="sk", api_fallback=True), True)
        else (_ for _ in ()).throw(AssertionError("lane misread"))))
    check("lane · a keyless org is always the subscription", lambda: (
        None if not supervisor.bills_the_key(_FakeOrg(), True)
        else (_ for _ in ()).throw(AssertionError("invented a key"))))

    # a SANDBOXED org's key need never appear in org.d: a kiosk-level key or
    # the ORGTREE_SANDBOX_API_KEY escape hatch reaches the container the same
    # way, and reading the org field alone called those turns "subscription"
    # (redteam 2026-08-18)
    _kiosk_keyed = _FakeOrg(slug="zz", kiosk={"sandbox": True,
                                              "api_key": "sk-kiosk"})
    _proxied = _FakeOrg(slug="zz", kiosk={"sandbox": True})
    check("lane · a sandboxed org with a KIOSK-level key bills that key",
          lambda: (
        None if supervisor.bills_the_key(_kiosk_keyed, False)
        else (_ for _ in ()).throw(AssertionError("missed the kiosk key"))))
    check("lane · a proxied sandbox is the subscription", lambda: (
        None if not supervisor.bills_the_key(_proxied, False)
        else (_ for _ in ()).throw(AssertionError("read proxied as a key"))))
    _envkey = os.environ.get("ORGTREE_SANDBOX_API_KEY")
    os.environ["ORGTREE_SANDBOX_API_KEY"] = "sk-escape"
    try:
        check("lane · …and the ORGTREE_SANDBOX_API_KEY escape hatch counts",
              lambda: (
            None if supervisor.bills_the_key(_proxied, False)
            else (_ for _ in ()).throw(AssertionError("missed the env key"))))
    finally:
        if _envkey is None:
            os.environ.pop("ORGTREE_SANDBOX_API_KEY", None)
        else:
            os.environ["ORGTREE_SANDBOX_API_KEY"] = _envkey
    check("lane · a sandboxed FALLBACK org counts a window open at spawn OR "
          "at freeze — the bridge flips per request, the capture is per turn",
          lambda: (
        None if supervisor.bills_the_key(
            _FakeOrg(slug="zz", kiosk={"sandbox": True}, api_key="sk",
                     api_fallback=True), True)
        and not supervisor.bills_the_key(
            _FakeOrg(slug="zz", kiosk={"sandbox": True}, api_key="sk",
                     api_fallback=True), False)
        else (_ for _ in ()).throw(AssertionError("sandboxed fallback lane"))))

    # ---- a readout ages into worthlessness --------------------------------
    _readout(("session", "session", 99, "critical", 3 * 3600, True, None))
    limits._cache["at"] = time.time() - limits.MAX_EVIDENCE_AGE - 1
    check("stale · a readout past MAX_EVIDENCE_AGE stops being evidence — a "
          "broken upstream serves the last good payload forever, and a "
          "freeze must not price a key window on a memory", lambda: (
        None if limits.reset_for("usage limit reached") == (None, "")
        else (_ for _ in ()).throw(AssertionError("spent on a stale readout"))))

    # ---- the critical one: a short lane may not borrow a long lane's reset -
    _readout(("session", "session", 99, "critical", -600, True, None),
             ("weekly_all", "weekly", 65, "normal", 6 * 86400, False, None))
    check("cap · a SESSION limit whose lane has expired in a stale readout "
          "must not be answered with the weekly lane six days out (that is "
          "six days of key billing)", lambda: (
        None if limits.reset_for("You've hit your session limit") == (None, "")
        else (_ for _ in ()).throw(AssertionError(
            limits.reset_for("You've hit your session limit")))))
    _readout(("session", "session", 99, "critical", -600, True, None),
             ("weekly_all", "weekly", 65, "normal", 3 * 3600, False, None))
    check("cap · …but another lane INSIDE the named lane's reach is a fine "
          "answer", lambda: (
        None if limits.reset_for("You've hit your session limit")[1]
        == "usage:weekly_all"
        else (_ for _ in ()).throw(AssertionError("over-tight cap"))))

    # ---- classify does not read a model ID as a tier limit ----------------
    for _blob, _want in (
            ("Claude AI usage limit reached (model claude-opus-4-1)",
             (None, None)),
            ("usage limit reached; switch models with /model (sonnet, haiku)",
             (None, None)),
            ("You've reached your Fable 5 limit. Run /usage-credits",
             ("weekly_scoped", "fable"))):
        check("classify · %r" % _blob[:44], (
            lambda b=_blob, w=_want: (
                None if limits.classify(b) == w
                else (_ for _ in ()).throw(AssertionError(limits.classify(b))))))

    # ---- the warm loop's cadence ------------------------------------------
    # the synthetic board is board-specific: drop it so the checks below
    # (and anything appended later) do not silently inherit it
    limits.invalidate()
    check("warm · an idle account is read every 5 min", lambda: (
        None if supervisor._warm_interval(12) == 300
        else (_ for _ in ()).throw(AssertionError("idle cadence"))))
    check("warm · 80% tightens to 2 min", lambda: (
        None if supervisor._warm_interval(80) == 120
        else (_ for _ in ()).throw(AssertionError("warning cadence"))))
    check("warm · 99% tightens to 45 s — a freeze is minutes away and the "
          "stamp it reads must not be older than that", lambda: (
        None if supervisor._warm_interval(99) == 45
        else (_ for _ in ()).throw(AssertionError("critical cadence"))))
    check("warm · a 'critical' severity counts as 95% whatever percent says",
          lambda: (
        None if (_readout(("session", "session", 3, "critical", 3600, True,
                           None)) or limits.pressure() >= 95)
        else (_ for _ in ()).throw(AssertionError(limits.pressure()))))

    # ---- W4 · the tick scheduled at the reset boundary itself -------------
    # A lane publishes its reset minutes-to-days ahead, so the one moment the
    # cached board is guaranteed wrong is knowable in advance. The cadence
    # alone would meet it up to five idle minutes late.
    limits.invalidate()
    check("reset-wake · with nothing cached there is no boundary to aim at",
          lambda: (
        None if limits.next_reset() is None
        else (_ for _ in ()).throw(AssertionError(limits.next_reset()))))

    _readout(("session", "session", 40, "normal", 90, True, None),
             ("weekly_all", "weekly", 60, "normal", 5 * 86400, False, None))
    check("reset-wake · the SOONEST future lane owns the boundary — the wake "
          "is a clock, not a bill, so any lane may own it", lambda: (
        None if abs((limits.next_reset() or 0) - (time.time() + 90)) < 5
        else (_ for _ in ()).throw(AssertionError(limits.next_reset()))))
    check("reset-wake · a 90 s boundary cuts the 5-min idle cadence to land "
          "just past it, not on it — the upstream rolls the window over on "
          "ITS clock", lambda: (
        None if (lambda n: abs(supervisor._warm_sleep(12, n + 90, n)
                               - (90 + supervisor.RESET_LAG)) < 0.01)(
                                   time.time())
        else (_ for _ in ()).throw(AssertionError(
            supervisor._warm_sleep(12, time.time() + 90, time.time())))))
    check("reset-wake · a boundary already at the door is floored at "
          "WARM_MIN_SLEEP — a skewed clock must not spin the loop against a "
          "semi-documented endpoint", lambda: (
        None if (lambda n: supervisor._warm_sleep(99, n + 0.2, n)
                 == supervisor.WARM_MIN_SLEEP)(time.time())
        else (_ for _ in ()).throw(AssertionError(
            supervisor._warm_sleep(99, time.time() + 0.2, time.time())))))
    check("reset-wake · a boundary FURTHER out than the cadence never "
          "lengthens it — the pressure bands stay the ceiling", lambda: (
        None if (lambda n: (supervisor._warm_sleep(99, n + 5 * 86400, n) == 45
                            and supervisor._warm_sleep(12, n + 5 * 86400, n)
                            == 300))(time.time())
        else (_ for _ in ()).throw(AssertionError("cadence ceiling"))))
    check("reset-wake · no boundary on the board leaves the cadence exactly "
          "as it was", lambda: (
        None if supervisor._warm_sleep(80, None, time.time()) == 120
        else (_ for _ in ()).throw(AssertionError("no-boundary cadence"))))

    _readout(("session", "session", 40, "normal", -60, True, None))
    check("reset-wake · a reset that has already passed is not a boundary — "
          "a stale board must not aim the loop at a time in the past",
          lambda: (
        None if limits.next_reset() is None
        else (_ for _ in ()).throw(AssertionError(limits.next_reset()))))
    _readout(("session", "session", 40, "normal", 30 * 86400, True, None))
    check("reset-wake · a reset past MAX_HORIZON is a number in a timestamp's "
          "seat, not a boundary", lambda: (
        None if limits.next_reset() is None
        else (_ for _ in ()).throw(AssertionError(limits.next_reset()))))
    limits.invalidate()

    # …and the wiring, because every check above passes on a loop that never
    # asks for a boundary (mutation: delete the call, suite stays green)
    def _the_loop_aims_at_the_boundary():
        j = _code.find("def start_usage_warm_loop(")
        fixture(j > 0, "the warm loop moved — re-read this check")
        seg = _code[j:_code.find(chr(10) + "def ", j + 1)]
        assert "limits.next_reset(" in seg and "_warm_next(" in seg, (
            "the loop must read the next reset and take its sleep from the "
            "step function — without this it is back to the pressure cadence "
            "alone, and every check above still passes")
    check("reset-wake · the loop actually aims at the next reset "
          "(structural)", _the_loop_aims_at_the_boundary)

    # …and the rollover-lag branch, which only ever happens when the host
    # clock and the upstream's disagree — unreachable from a live loop
    _n = time.time()
    check("reset-wake · an ordinary tick carries the boundary it aims at and "
          "no misses", lambda: (
        None if supervisor._warm_next(None, 0, _n + 90, 12, _n)
        == (90 + supervisor.RESET_LAG, _n + 90, 0)
        else (_ for _ in ()).throw(AssertionError(
            supervisor._warm_next(None, 0, _n + 90, 12, _n)))))
    check("reset-wake · a boundary beyond the sleep is NOT recorded as aimed "
          "at — otherwise the next tick reads a cadence wake as a missed "
          "rollover and re-asks for nothing", lambda: (
        None if supervisor._warm_next(None, 0, _n + 5 * 86400, 12, _n)
        == (300.0, None, 0)
        else (_ for _ in ()).throw(AssertionError(
            supervisor._warm_next(None, 0, _n + 5 * 86400, 12, _n)))))
    check("reset-wake · waking AT the boundary onto a board with no new "
          "window re-asks in WARM_MIN_SLEEP, the aim standing — the upstream "
          "rolls over on its own clock", lambda: (
        None if supervisor._warm_next(_n - 1, 0, None, 12, _n)
        == (supervisor.WARM_MIN_SLEEP, _n - 1, 1)
        else (_ for _ in ()).throw(AssertionError(
            supervisor._warm_next(_n - 1, 0, None, 12, _n)))))
    check("reset-wake · the re-asking is BOUNDED — an account with no lanes "
          "at all looks identical from here, and must not be polled every "
          "10 s forever", lambda: (
        None if supervisor._warm_next(
            _n - 1, supervisor.RESET_RECHECKS, None, 12, _n) == (300.0, None,
                                                                 0)
        else (_ for _ in ()).throw(AssertionError(
            supervisor._warm_next(_n - 1, supervisor.RESET_RECHECKS, None, 12,
                                  _n)))))
    check("reset-wake · a rollover that DOES land clears the miss count and "
          "aims at the new window", lambda: (
        None if supervisor._warm_next(_n - 1, 2, _n + 18000, 12, _n)
        == (300.0, None, 0)
        else (_ for _ in ()).throw(AssertionError(
            supervisor._warm_next(_n - 1, 2, _n + 18000, 12, _n)))))


# ══════════════════════════════════════════════════════════════════════════ §7

def sec_died_in_flight() -> None:
    """§7 — THE TURN THAT DIED AND WAS NEVER RE-DRIVEN (user incident
    2026-08-21).

    The machinery §4 exercises was already right. What reached it was not:
    `_looks_like_connection_failure` can only match text the CLI WROTE, and
    the connection that drops mid-response takes the CLI down too hard to
    write anything. stderr empty, `errors: []` empty, so orgtree synthesized
    "the CLI exited 1 without writing anything to stderr" — matching no errno
    spelling — and the turn fell through to the terminal bucket. No freeze
    record, so nothing to resume; no notification, so nobody knew. A live
    agent sat idle with five uncommitted files for two hours until a human
    happened to look.

    So this section measures the two halves that must BOTH hold: the incident
    shape is now retried, and the shapes that merely LOOK like it from the
    outside — same exit code, same silence — still are not."""
    print("\n§7 the turn that died mid-response — retried, or abandoned?")

    # ── the predicate alone, no rig: the truth table is the safety argument ──
    f = supervisor._died_in_flight
    check("classify · the INCIDENT shape (exit code only, model had spoken, "
          "no boundary reached) is transient",
          lambda: (None if f(exit_only=True, started=True, boundary=False)
                   else (_ for _ in ()).throw(AssertionError(
                       "the 2026-08-21 shape is not classified transient — "
                       "this is the bug, unfixed"))))
    check("classify · a nonzero exit WITH a real error is NOT transient — "
          "evidence is never overridden",
          lambda: (None if not f(exit_only=False, started=True, boundary=False)
                   else (_ for _ in ()).throw(AssertionError(
                       "a reported error was reclassified as transient"))))
    check("classify · a CLI that died before the model ever spoke is NOT "
          "transient (bad argv, missing CLI, unreadable config)",
          lambda: (None if not f(exit_only=True, started=False, boundary=False)
                   else (_ for _ in ()).throw(AssertionError(
                       "a launch failure would be retried — this is the "
                       "crash-loop hazard the classifier exists to avoid"))))
    check("classify · a turn that REACHED its boundary and then exited "
          "nonzero is a straggler, not a casualty",
          lambda: (None if not f(exit_only=True, started=True, boundary=True)
                   else (_ for _ in ()).throw(AssertionError(
                       "a completed turn would be retried"))))

    if not shutil.which("node"):
        note("node is not on PATH — §7's end-to-end half skipped")
        return

    # ── and now through the REAL turn loop, which is what actually broke ────
    slug, nid = probe_org()

    def _incident_is_retried() -> None:
        """THE CHECK THIS WHOLE BRANCH EXISTS FOR. Revert the fix and this is
        the one that goes red: the node ends UNFROZEN with no `net_fail_run`,
        exactly as restart-tool's did, and nothing in the backend would ever
        drive it again."""
        set_mode("died-in-flight")
        run_turn(slug, nid, "please do the thing")
        n = node(slug, nid)
        assert n.get("frozen"), (
            "the turn that died mid-response left NO freeze record — the node "
            "is abandoned exactly as in the incident. Nothing re-drives it: "
            "▶ finds no record and no timer owns it. "
            f"(net_fail_run={n.get('net_fail_run')!r})")
        assert n["frozen"].get("connection"), (
            "frozen, but not as the connection kind — so the auto-resume "
            "timer's D-122 toggle-independent wake does not own it: "
            f"{n['frozen']}")
        assert (n.get("net_fail_run") or 0) == 1, (
            "the shared attempt counter did not move, so the ceiling is not "
            f"counting this class: {n.get('net_fail_run')!r}")
    check("retry · a CLI that dies mid-response with an exit code and NOTHING "
          "else is frozen for retry, not abandoned (THE INCIDENT)",
          _incident_is_retried)

    def _marker_warns_before_redoing() -> None:
        """The replay lands in the SAME session, so the agent resumes with its
        partial work in view — but a BARE replay is indistinguishable from the
        message merely arriving, and the effects a dying turn commits are the
        non-idempotent ones (mail already sent, a suite already spawned on
        fixed ports). The victim supplied both cases first-hand."""
        rt = (node(slug, nid).get("frozen") or {}).get("resume_texts") or []
        assert rt, "nothing was kept to replay — the driving message is lost"
        body = rt[-1]
        assert "please do the thing" in body, (
            f"the original message is not in the replay: {body[:200]!r}")
        low = body.lower()
        assert "retried" in low and "not undone" in low, (
            "the replay does not tell the agent it IS a retry, so nothing "
            f"prompts it to check state before redoing: {body[:300]!r}")
        # ⚠ the trap the victim hit personally: it ANNOUNCED an edit in prose
        # and died before the tool call ran, so the file was untouched while
        # its own transcript said otherwise. An agent reading back its last
        # message concludes the exact opposite of the truth — and it is the
        # most natural thing in the world for it to do on resume.
        assert "last message" in low and "disk" in low, (
            "the replay does not warn that announced work may never have "
            "run. A turn that died mid-response can leave prose describing "
            "an edit with no edit behind it, so the transcript is evidence "
            f"of INTENT, not of effect: {body[:400]!r}")
    check("marker · the replayed text names the retry and warns that "
          "already-committed effects were NOT undone", _marker_warns_before_redoing)

    def _honest_label() -> None:
        lbl = ((node(slug, nid).get("frozen") or {}).get("until") or "").lower()
        assert "network" not in lbl, (
            "the freeze blames the NETWORK, but the shape classifier saw only "
            "a CLI that died having written no reason at all — this sends the "
            f"next debugger after a router that is probably fine: {lbl!r}")
        assert "attempt" in lbl, f"the label states no attempt: {lbl!r}"
    check("label · a shape-classified death does not claim to be a network "
          "interruption — it says what was actually observed", _honest_label)

    # ── the two look-alikes. Same exit code, same silence, must NOT retry ───
    def _never_started_stays_terminal() -> None:
        """THE CONTROL, and it is not decoration: it is what separates this
        fix from `retry any failure`, which would turn a bad argv into an
        infinite loop burning turn slots and real money."""
        s2, n2 = probe_org()
        set_mode("dead-on-arrival")
        run_turn(s2, n2, "hello")
        n = node(s2, n2)
        assert not n.get("frozen"), (
            "a CLI that died before the model ever spoke was scheduled for "
            f"retry — this is the crash-loop hazard: {n['frozen']}")
        assert not n.get("net_fail_run"), (
            f"…and it is counting attempts against it: {n.get('net_fail_run')!r}")
    check("control · a CLI that dies before the model ever speaks stays "
          "TERMINAL — the fix is not a catch-all", _never_started_stays_terminal)

    def _reported_error_stays_terminal() -> None:
        s3, n3 = probe_org()
        set_mode("died-with-stderr")
        run_turn(s3, n3, "hello")
        n = node(s3, n3)
        assert not n.get("frozen"), (
            "a nonzero exit carrying a REAL error on stderr was reclassified "
            f"as transient — evidence must never be overridden: {n['frozen']}")
    check("control · a nonzero exit WITH a real error on stderr stays "
          "TERMINAL", _reported_error_stays_terminal)

    # ── bounded, and loud when it gives up ─────────────────────────────────
    def _bounded_then_loud() -> None:
        """The ceiling is the same one the connection class uses, off the same
        counter — deliberately shared, so a node flapping between the two gets
        four attempts in total rather than four each. And at exhaustion the
        silence has to end: the agent is told (it holds the uncommitted work)
        and so is its superior (nobody was told, and that was the harm)."""
        org = store.create_org("zz inflight loud")
        boss = org.hire(USER, None, "haiku", 20, "boss", add_dirs=[],
                        tools={"bash": False, "web": False, "edit": False,
                               "subagents": False, "mcp": []},
                        org_visibility="team", charter="boss")["node"]
        kid = org.hire(boss, boss, "haiku", 5, "kid", add_dirs=[],
                       tools={"bash": False, "web": False, "edit": False,
                              "subagents": False, "mcp": []},
                       org_visibility="team", charter="kid")["node"]
        store.save_org(org)
        s4 = org.d["slug"]
        set_mode("died-in-flight")

        for i in range(supervisor.NET_RETRY_MAX):
            run_turn(s4, kid, "keeps dying")
            n = node(s4, kid)
            fixture(bool(n.get("frozen")),
                    f"attempt {i + 1} did not freeze "
                    f"(run={n.get('net_fail_run')!r})")
            fixture(not (store.load_org(s4).d.get("mail_log", {}).get(boss)),
                    f"the superior was told at attempt {i + 1} — the announce "
                    f"must fire ONCE at exhaustion, never per attempt")
            # un-park by clearing the record, never via resume_frozen: that
            # spawns a replay which fails on the same dead CLI and races the
            # counter past the cap (the trap §4 documents)
            o = store.load_org(s4)
            o.nodes[kid].pop("frozen", None)
            store.save_org(o)

        run_turn(s4, kid, "and again")           # the attempt past the cap
        n = node(s4, kid)
        assert (n.get("net_fail_run") or 0) > supervisor.NET_RETRY_MAX, \
            f"never reached the terminal attempt: {n.get('net_fail_run')!r}"
        assert not n.get("frozen"), (
            "still frozen past the cap — the retry is not bounded and would "
            f"go round forever: {n['frozen']}")

        # ⚠ mail_log, NOT the `mail` queue. The queue is the undelivered half:
        # the announce DRIVES both recipients, and a driven turn drains its
        # mailbox on the way in — so reading `mail` races delivery and can
        # report "nobody was told" precisely BECAUSE they were. mail_log is
        # the durable record every sender mirrors into and nothing drains.
        box = store.load_org(s4).d.get("mail_log", {})
        kid_mail = [m for m in box.get(kid, []) if m.get("from") == "@system"]
        boss_mail = [m for m in box.get(boss, []) if m.get("from") == "@system"]
        assert kid_mail, (
            "orgtree gave up and told the AGENT nothing — it is the only "
            "party holding the uncommitted work, and from the inside a failed "
            "turn is indistinguishable from nobody having messaged it")
        assert boss_mail, (
            "orgtree gave up and told the SUPERIOR nothing. This is the "
            "incident's actual harm: from one level up, an abandoned agent "
            "and a working one look identical, so recovery waits on a human "
            "happening to notice")
        assert kid_mail[-1]["kind"] == "message", (
            "the notice is a no-wake kind, so it lands in a box that is read "
            f"at a next turn which by construction never comes: {kid_mail[-1]['kind']!r}")
        assert "not undone" in kid_mail[-1]["body"].lower(), (
            "the give-up mail does not warn that committed effects survive, "
            f"so the agent may blindly redo them: {kid_mail[-1]['body'][:200]!r}")
        _once[0] = (s4, kid, boss, len(boss_mail))
    check("bounded+loud · the retries stop at the cap, and BOTH the agent and "
          "its superior are told durably when they do", _bounded_then_loud)

    def _announced_exactly_once() -> None:
        """The announce DRIVES the node, and a driven turn that dies the same
        way lands back on the same branch. Guarded by `== MAX + 1` rather than
        the enclosing `> MAX`: on `>` the fail-loud path would announce, drive,
        die, announce… — the unbounded retry loop this change exists to
        prevent, rebuilt inside the fix for it."""
        assert _once[0], "the exhaustion fixture did not run"
        s4, kid, boss, before = _once[0]
        assert before == 1, f"the superior was told {before} times, not once"
        for _ in range(3):
            run_turn(s4, kid, "still dying")
        after = len([m for m in
                     store.load_org(s4).d.get("mail_log", {}).get(boss, [])
                     if m.get("from") == "@system"])
        assert after == 1, (
            f"the superior was told {after} times across further failures — "
            f"the give-up announce is re-firing, so it is driving the node on "
            f"every failure past the cap instead of once")
    check("bounded+loud · …and it announces EXACTLY ONCE, however many more "
          "turns fail after it", _announced_exactly_once)


# ══════════════════════════════════════════════════════════════════════════ §8

def sec_deploy_window() -> None:
    """§8 — D-142/a, THE DEPLOY KILL WINDOW.

    Same bug class as §7, arriving from outside: the mid-turn refusal that
    guards a deploy is consulted at T=0, but the kill lands minutes later
    after pull + npm + build. An agent woken by mail inside that gap is
    started and then cut mid-turn.

    The fix HOLDS the turn at `_run_turn`'s door rather than refusing it, so
    no mail is ever dequeued — see the comment there. These checks measure
    that the hold happens, that it is bounded, that it releases, and that the
    mail which arrived during the window is still delivered exactly once."""
    print("\n§8 the deploy kill window — held, bounded, released")

    class _Child:
        """A deploy child under our control."""
        def __init__(self) -> None:
            self._done = threading.Event()
            self.returncode = None

        def wait(self, timeout=None):                        # noqa: ARG002
            self._done.wait()
            return 0

        def finish(self) -> None:
            self.returncode = 0
            self._done.set()

    def _held_then_released() -> None:
        """THE CENTRAL CHECK: a turn starting inside the window does not run
        until the deploy child exits."""
        child = _Child()
        supervisor._arm_deploy_window(child)
        try:
            assert not supervisor._deploy_done.is_set(), \
                "arming a live deploy child did not close the window"
            started: list[float] = []

            def _turn() -> None:
                supervisor._hold_for_deploy("zz", "probe")
                started.append(time.monotonic())
            t = threading.Thread(target=_turn, daemon=True)
            t.start()
            t.join(0.6)
            assert not started, (
                "the turn started WHILE a deploy child was alive — this is "
                "the kill window: pull+npm+build still has minutes to run, "
                "and the restart will cut this turn mid-flight")
            child.finish()
            t.join(5.0)
            assert started, (
                "the deploy child exited but the held turn never started — "
                "the window never reopened, so every org on this machine is "
                "wedged until the backend restarts")
        finally:
            child.finish()
            supervisor._deploy_done.set()
    check("hold · a turn beginning inside the deploy window waits for the "
          "child, then runs", _held_then_released)

    def _nothing_spawned_never_holds() -> None:
        """THE CONTROL. A refusal (or a spawn that raised) means no deploy is
        coming, so arming would hold the machine for a kill that never
        arrives.

        ⚠ ASSERTED ON `clear()` HAVING BEEN CALLED, not on the flag's value
        afterwards. The obvious version — set the flag, arm with None, assert
        it is still set — PASSED against a mutant that armed unconditionally,
        and my own mutation run caught it. With arming forced on, the watcher
        thread calls `None.wait()`, throws, and its `finally` re-opens the
        window microseconds later; the assertion then read a recovered error
        as correct behaviour. A timing race that resolves the right way is an
        abstention, and an abstention reads exactly like a pass. Counting the
        clear is deterministic: it cannot be undone by a later set."""
        class _Spy(threading.Event):
            def __init__(self) -> None:
                super().__init__()
                self.clears = 0

            def clear(self) -> None:
                self.clears += 1
                super().clear()

        spy = _Spy()
        spy.set()
        real = supervisor._deploy_done
        supervisor._deploy_done = spy                # type: ignore[assignment]
        try:
            supervisor._arm_deploy_window(None)
            assert spy.clears == 0, (
                "nothing was spawned, yet the window was CLOSED — every org "
                "on this machine would wait for a deploy that does not "
                "exist. (A watcher that immediately errors and reopens it "
                "does not make this safe; it makes it racy.)")
        finally:
            supervisor._deploy_done = real           # type: ignore[assignment]
    check("control · a spawn that did not happen opens no window",
          _nothing_spawned_never_holds)

    def _gate_is_actually_wired() -> None:
        """`hold ·` exercises the helper DIRECTLY, so it would stay green even
        if the gate were never called from `_run_turn`. The end-to-end `mail ·`
        check does cover the wiring — but it needs `node`, and on a box
        without it nothing would. Read the real function body (comments
        stripped, so a mention in prose cannot satisfy it)."""
        import inspect                                        # noqa: PLC0415
        body = "\n".join(
            ln for ln in inspect.getsource(supervisor._run_turn).splitlines()
            if not ln.strip().startswith("#"))
        assert "_hold_for_deploy(" in body, (
            "_run_turn does not call _hold_for_deploy, so no turn is gated "
            "and the deploy kill window is wide open — whatever the unit "
            "checks above say about the helper in isolation")
    check("wiring · the gate is actually called from _run_turn, not merely "
          "defined next to it", _gate_is_actually_wired)

    def _bounded() -> None:
        """A deploy that never exits must not silence the machine forever."""
        assert supervisor.DEPLOY_HOLD_MAX <= 900, (
            f"the hold ceiling is {supervisor.DEPLOY_HOLD_MAX}s — long enough "
            f"that a wedged deploy looks exactly like a dead orgtree")
        child = _Child()
        supervisor._arm_deploy_window(child)
        real = supervisor.DEPLOY_HOLD_MAX
        try:
            supervisor.DEPLOY_HOLD_MAX = 0.3     # a deploy that never exits
            t0 = time.monotonic()
            supervisor._hold_for_deploy("zz", "probe")
            held = time.monotonic() - t0
            assert held < 3.0, (
                f"the hold did not give up at its ceiling ({held:.1f}s) — an "
                f"unbounded hold on a wedged deploy is a worse outage than "
                f"the mid-turn kill it prevents")
        finally:
            supervisor.DEPLOY_HOLD_MAX = real
            child.finish()
            supervisor._deploy_done.set()
    check("bounded · a deploy that never exits releases at the ceiling "
          "instead of wedging the machine forever", _bounded)

    def _refusal_is_watchable() -> None:
        """THE TRAP, CLOSED. `_detached_spawn` now returns a handle so the
        window can watch it. If the test interlock's REFUSAL leg kept
        returning None — and refusal is the only path the deploy checks ever
        take — then `_arm_deploy_window` would take its "nothing spawned"
        early return and the watcher would never once run under test. Green
        suite, unexercised production path. So assert the refusal hands back
        something waitable, positively."""
        import _no_deploy                                     # noqa: PLC0415
        got = _no_deploy._interlock(
            ["powershell", "-File", "update.ps1"], ".", os.devnull)
        assert got is not None, (
            "the interlock's refusal returns None, so every deploy check in "
            "every suite skips the watcher entirely — the window code would "
            "ship having never been executed by a single test")
        assert hasattr(got, "wait") and got.wait() == 0, (
            f"the refusal handed back something the deploy watcher cannot "
            f"wait on: {got!r}")
        assert _no_deploy.ATTEMPTS, (
            "the refusal was not RECORDED — mcptool's 'no real deploy, and "
            "it did try' check goes vacuous without this half")
        _no_deploy.ATTEMPTS.clear()
    check("trap · a REFUSED deploy still returns a watchable handle, so the "
          "window is exercised by tests rather than skipped",
          _refusal_is_watchable)

    if not shutil.which("node"):
        note("node is not on PATH — §8's mail-delivery half skipped")
        return

    def _mail_survives_the_window() -> None:
        """COORDINATOR'S CONDITION (c): a check that FAILS IF MAIL IS DROPPED,
        not one that passes when it isn't. The message is sent while the
        window is shut, so it is admitted and held; when the child exits the
        turn must run and the CLI must actually be handed that text — EXACTLY
        once. Asserted on a POSITIVE marker (what the CLI was served), never
        on an absence."""
        slug, nid = probe_org()
        set_mode("plain")
        child = _Child()
        supervisor._arm_deploy_window(child)
        try:
            supervisor.send_message(slug, nid, "MAILMARKER-D142")
            time.sleep(0.5)
            assert not [s for s in served() if "MAILMARKER-D142" in s], (
                "the message reached the CLI while a deploy child was alive "
                "— that turn is inside the kill window")
            child.finish()
            for _ in range(150):
                if [s for s in served() if "MAILMARKER-D142" in s]:
                    break
                time.sleep(0.1)
        finally:
            child.finish()
            supervisor._deploy_done.set()
        hits = [s for s in served() if "MAILMARKER-D142" in s]
        assert hits, (
            "the message sent during the deploy window NEVER reached the CLI "
            "after the window closed. It was accepted and then lost — the "
            "exact failure every reviewer predicted for this change")
        assert len(hits) == 1, (
            f"the held message was delivered {len(hits)} times — holding a "
            f"turn must not duplicate the mail it was holding: {hits}")
    check("mail · a message accepted during the window is delivered EXACTLY "
          "ONCE after it, never dropped", _mail_survives_the_window)


# ══════════════════════════════════════════════════════════════════════════ §9

def _team(n: int, name: str) -> tuple[str, str, list[str]]:
    org = store.create_org(f"zz {name}")
    tl = {"bash": False, "web": False, "edit": False, "subagents": False,
          "mcp": []}
    boss = org.hire(USER, None, "haiku", 60, "boss", add_dirs=[], tools=tl,
                    org_visibility="team", charter="b")["node"]
    kids = [org.hire(boss, boss, "haiku", 5, f"kid{i}", add_dirs=[], tools=tl,
                     org_visibility="team", charter="k")["node"]
            for i in range(n)]
    store.save_org(org)
    return org.d["slug"], boss, kids


def _sys_mail(slug: str, nid: str, needle: str = "") -> list[dict]:
    log = store.load_org(slug).d.get("mail_log", {}).get(nid, [])
    return [m for m in log if m.get("from") == "@system"
            and (not needle or needle in m.get("body", ""))]


def sec_abandoned() -> None:
    """§9 — THE TERMINAL BUCKET, MADE LOUD.

    §7 covered the class orgtree RETRIES. This covers the classes it
    deliberately does not — a turn killed by the watchdog or the budget, a
    CLI that died before the model spoke, an exit carrying a real error.
    Retrying those would be wrong, but until now they left the node live,
    unfrozen, holding a `turn_error_log` row nobody opens: from one level up,
    indistinguishable from an agent quietly working.

    The bound is the interesting half, so it is measured first and hardest:
    the failing agent is NEVER driven (so the announcement cannot become its
    own retry loop), one announcement per failure RUN across every door, and
    a superior is woken at most once per window however many reports die."""
    print("\n§9 the turn nothing retries — abandoned, or announced?")

    if not shutil.which("node"):
        note("node is not on PATH — §9 skipped (it needs the CLI stand-in)")
        return

    # ── THE BOUND, first ───────────────────────────────────────────────────
    def _failing_agent_is_never_driven() -> None:
        """THE STRUCTURAL BOUND. If the CLI cannot start, driving the agent
        spawns another CLI that cannot start — the announcement becomes its
        own retry loop, which is the M5 trap from the previous branch. Here
        it is not guarded against, it is impossible: the failing node is
        never sent a drive at all. Measured POSITIVELY — count the turns the
        CLI was actually handed, not the absence of a symptom."""
        slug, _boss, (kid,) = _team(1, "abandon-nodrive")
        set_mode("dead-on-arrival")
        run_turn(slug, kid, "go")
        time.sleep(1.2)
        # ⚠ count THIS NODE's turns, not the rig's. `served()` is machine-wide
        # and the superior IS driven (intentionally), so counting envelopes
        # measured the wrong node and failed for the right behaviour. Every
        # failed turn writes exactly one turn_error_log row on the node that
        # ran it, which is a per-node positive marker.
        rows = store.load_org(slug).d.get("turn_error_log", {}).get(kid, [])
        assert len(rows) == 1, (
            f"the failing agent ran {len(rows)} turns for ONE failure — the "
            f"announcement is driving the node that just died, so a "
            f"persistent failure becomes a loop")
    check("bound · the FAILING agent is never driven — one failure costs "
          "exactly one CLI turn, so the announcement cannot loop",
          _failing_agent_is_never_driven)

    def _top_level_is_not_driven_either() -> None:
        """THE CARVE-OUT THAT WASN'T. A node with no superior has nobody to
        wake, and an earlier version made it the one exception — drive the
        agent, since it is the only actor that exists.

        `test_turn_lifecycle`'s `clicrash · exactly one copy on screen` went
        red: a turn killed mid-flight folds its unconfirmed batch back into
        the mailbox, that extra turn DRAINED it, and the message was echoed
        into the transcript a second time. Two bubbles, permanently, for a
        message the user sent once. The exception bought a doomed turn and
        paid for it with a visible duplicate, so it is gone — and the rule is
        now true with no carve-out. Measured per-node, not on `served()`."""
        org = store.create_org("zz abandon-toplevel")
        tl = {"bash": False, "web": False, "edit": False, "subagents": False,
              "mcp": []}
        solo = org.hire(USER, None, "haiku", 20, "solo", add_dirs=[], tools=tl,
                        org_visibility="team", charter="s")["node"]
        store.save_org(org)
        slug = org.d["slug"]
        set_mode("dead-on-arrival")
        run_turn(slug, solo, "go")
        time.sleep(1.2)
        rows = store.load_org(slug).d.get("turn_error_log", {}).get(solo, [])
        assert len(rows) == 1, (
            f"a top-level node ran {len(rows)} turns for ONE failure — it is "
            f"being driven by its own abandonment, which re-delivers the "
            f"folded-back mail and duplicates it on the user's screen")
        assert _sys_mail(slug, solo), (
            "…and it was left no durable mail either, so the failure is "
            "invisible to the user whose desk it sits on")
        # ⚠ THE TOP OF THE TREE. Every announcement terminates upward at a
        # node with no superior, and a top-level coordinator IS that node —
        # so without this the one agent the user actually watches is the only
        # one that cannot report its own death. Measured before it was fixed:
        # `user_inbox` held ZERO entries.
        inbox = store.load_org(slug).d.get("user_inbox", [])
        hits = [m for m in inbox if solo in (m.get("body") or "")]
        assert hits, (
            "a top-level node died terminally and the USER'S INBOX got "
            f"nothing ({len(inbox)} entries) — the chain goes silent exactly "
            f"where there is nobody left to notice, which is the failure this "
            f"whole piece exists to delete")
        assert "How it died" in hits[0]["body"], (
            f"the user is told it stopped but not HOW: {hits[0]['body'][:160]!r}")
    check("bound · a node with NO superior is not driven either — the mail "
          "waits on its desk instead", _top_level_is_not_driven_either)

    def _once_per_run() -> None:
        slug, boss, (kid,) = _team(1, "abandon-once")
        set_mode("dead-on-arrival")
        for i in range(4):
            run_turn(slug, kid, f"try {i}")
        time.sleep(0.6)
        told = _sys_mail(slug, boss, "REPORT STALLED")
        assert len(told) == 1, (
            f"four consecutive terminal failures produced {len(told)} "
            f"announcements — an agent stuck in a broken state would mail "
            f"its superior on every message it ever receives")
        assert (node(slug, kid).get("hard_fail_run") or 0) == 4, (
            "the run counter did not advance with the failures: "
            f"{node(slug, kid).get('hard_fail_run')!r}")
    check("bound · N consecutive terminal failures announce exactly ONCE",
          _once_per_run)

    def _rearmed_by_a_completed_turn() -> None:
        """The counter must CLEAR, or an agent that recovers and later breaks
        again is silently swallowed as 'already told them'."""
        slug, boss, (kid,) = _team(1, "abandon-rearm")
        set_mode("dead-on-arrival")
        run_turn(slug, kid, "fail")
        time.sleep(0.4)
        set_mode("plain")
        run_turn(slug, kid, "works now")
        assert not node(slug, kid).get("hard_fail_run"), (
            "a completed turn did not clear the run counter: "
            f"{node(slug, kid).get('hard_fail_run')!r}")
        set_mode("dead-on-arrival")
        run_turn(slug, kid, "fail again")
        time.sleep(0.6)
        told = _sys_mail(slug, boss, "REPORT STALLED")
        assert len(told) == 2, (
            f"a NEW failure episode after a working turn was not announced "
            f"({len(told)} total) — the agent broke twice and the second "
            f"time went unreported")
    check("bound · …and a completed turn re-arms it, so a second episode is "
          "announced again", _rearmed_by_a_completed_turn)

    def _firehose_is_bounded() -> None:
        """A machine-wide cause breaks every agent at once. MEASURED: repeated
        send_message DRIVES do NOT coalesce (three drives at an idle node gave
        three separate envelopes), while DEPOSITED mail does (three deposits +
        one drive gave one envelope carrying all three). So the mail is
        written every time and only the WAKE is throttled."""
        slug, boss, kids = _team(4, "abandon-fire")
        set_mode("dead-on-arrival")
        supervisor._abandon_drove.clear()
        # ⚠ count the WAKES DIRECTLY by instrumenting the drive seam. Deriving
        # them from `served()` arithmetic counted the superior's own failing
        # turns too and reported a firehose that was not there — the measure
        # has to name the thing it claims to measure.
        woke: list[str] = []
        real_send = supervisor.send_message

        def _spy(s: str, n: str, *a, **k):
            # ⚠ only wakes ABOUT A REPORT count. In this rig the fake CLI's
            # mode is machine-wide, so the superior's own turn fails too and
            # (being top-level) drives ITSELF — a real behaviour, but not a
            # firehose wake, and counting it reported one that wasn't there.
            if n == boss and a and "Your report" in str(a[0]):
                woke.append(n)
            return real_send(s, n, *a, **k)
        supervisor.send_message = _spy                # type: ignore[assignment]
        try:
            for k in kids:
                run_turn(slug, k, "die")
            time.sleep(1.5)
        finally:
            supervisor.send_message = real_send       # type: ignore[assignment]
        told = _sys_mail(slug, boss, "REPORT STALLED")
        assert len(told) == len(kids), (
            f"only {len(told)} of {len(kids)} dead reports were mailed to the "
            f"superior — throttling the DRIVE must never cost a NOTICE")
        assert len(woke) == 1, (
            f"the superior was woken {len(woke)} times for one machine-wide "
            f"cause — that is the firehose: a broken team costing its "
            f"superior a turn per report")
    check("bound · a machine-wide failure mails the superior about EVERY "
          "report but wakes it once", _firehose_is_bounded)

    # ── the doors ──────────────────────────────────────────────────────────
    def _door_launch() -> None:
        slug, boss, (kid,) = _team(1, "abandon-door2")
        set_mode("dead-on-arrival")
        run_turn(slug, kid, "go")
        time.sleep(0.5)
        told = _sys_mail(slug, boss, "REPORT STALLED")
        assert told, "a launch failure told the superior nothing"
        body = told[0]["body"].lower()
        assert "before the model ever spoke" in body, (
            "the announcement does not name WHICH door — a superior cannot "
            "tell whether to look at the machine or at the work: "
            f"{told[0]['body'][:200]!r}")
        assert "not been driven" in body, (
            "the superior is not told the agent was left asleep, so it may "
            "assume the agent is already retrying")
    check("door · a CLI that died before the model spoke is named as an "
          "environment fault, not a turn that went wrong", _door_launch)

    def _door_killed() -> None:
        """Door 1: the idle watchdog. Slow by nature — the dog wakes on a 5 s
        cadence — so this is the one check here that costs real seconds."""
        slug, boss, (kid,) = _team(1, "abandon-door1")
        set_mode("hang")
        real = supervisor.TURN_IDLE
        try:
            supervisor.TURN_IDLE = 1.0
            run_turn(slug, kid, "go quiet")
        finally:
            supervisor.TURN_IDLE = real
        time.sleep(0.8)
        told = _sys_mail(slug, boss, "REPORT STALLED")
        assert told, (
            "a turn KILLED by the idle watchdog told the superior nothing — "
            "the node is live, unfrozen and idle, and nothing retries a kill")
        assert "watchdog" in told[0]["body"].lower(), (
            f"the kill is not named as the door: {told[0]['body'][:200]!r}")
    check("door · a turn killed by the idle watchdog is announced, and named "
          "as a kill", _door_killed)

    def _shared_counter_across_doors() -> None:
        """⚠ ONE counter for BOTH doors. A node that is killed by the watchdog
        and then fails to launch is ONE broken episode, not two, and must not
        buy a second announcement by changing how it dies."""
        slug, boss, (kid,) = _team(1, "abandon-shared")
        set_mode("hang")
        real = supervisor.TURN_IDLE
        try:
            supervisor.TURN_IDLE = 1.0
            run_turn(slug, kid, "hang")
        finally:
            supervisor.TURN_IDLE = real
        time.sleep(0.5)
        set_mode("dead-on-arrival")          # a DIFFERENT door, same episode
        run_turn(slug, kid, "now fail to launch")
        time.sleep(0.5)
        told = _sys_mail(slug, boss, "REPORT STALLED")
        assert len(told) == 1, (
            f"a node that flapped between two doors announced {len(told)} "
            f"times — the counter is per-door, so a node failing in varied "
            f"ways mails its superior over and over")
    check("bound · the run counter is SHARED across doors — flapping between "
          "kill and launch-failure still announces once",
          _shared_counter_across_doors)

    def _success_announces_nothing() -> None:
        """THE CONTROL. Must SURVIVE every tightening above."""
        slug, boss, (kid,) = _team(1, "abandon-control")
        set_mode("plain")
        run_turn(slug, kid, "a perfectly fine turn")
        time.sleep(0.4)
        assert not _sys_mail(slug, boss), (
            "a SUCCESSFUL turn announced an abandonment to the superior — "
            "every working agent on the machine would be reported as broken")
        assert not _sys_mail(slug, kid), (
            "a successful turn left the agent an abandonment notice")
    check("control · a turn that SUCCEEDS announces nothing to anyone",
          _success_announces_nothing)


_once: list = [None]


def main() -> None:
    print("═══ usage-limit freeze — the shape the CLI actually reports ═══")
    sec_detect()
    sec_wake()
    sec_reset_timing()
    if not shutil.which("node"):
        note("node is not on PATH — §2/§3 skipped (they need the CLI stand-in)")
    else:
        sec_shapes()
        sec_reader()
        sec_attack_the_fix()
    sec_died_in_flight()      # its predicate half needs no rig
    sec_deploy_window()       # D-142/a — most of it needs no rig either
    sec_abandoned()           # the terminal bucket, made loud

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
