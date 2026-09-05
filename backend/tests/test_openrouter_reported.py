"""What the CLI REPORTED about the messages it delivered, per turn — and the
`modelUsage` key the cost path looks up (audit C-2).

    python backend/tests/test_openrouter_reported.py   (no pytest)

THREE KINDS OF EVIDENCE, KEPT APART
-----------------------------------
MEASURED, in a captured CLI transcript on this machine (2.1.258, sdk-cli, an
orgtree agent on `x-ai/grok-4.6` through openrouter.ai, 2026-09-03): on 560
assistant records, `message.provider` is "xAI" on 559 and absent on the one
`<synthetic>` record; `message.model` is `x-ai/grok-4.6` — an ECHO of the id
that was requested — on all 560; `message.id` is on all 560; and the
record-level `requestId` carries OpenRouter's own generation id
(`gen-1788438176-yR5GCU6aK7r3mDkmlyqi`).

SOURCE-DERIVED, read out of that same build's binary (2026-09-05, read-only;
no turn was run): the stdout projection for an assistant message passes the
`message` object BY REFERENCE and adds `request_id` when the message has one.
That is why those fields are expected on the wire. ⚠ It is NOT a captured
stdout observation — nobody has read a live OpenRouter stdout stream here.
See codex-delivery/evidence/cli-stdout-shape-2.1.258.md.

NOT OBSERVED, and never asserted: which machine actually served a turn. On a
gateway lane the reported model is an echo of the request, so everything here
is labelled REPORTED and the summary keeps every distinct value and says when
they differ rather than choosing one.

    §1  the collector and the summary (pure)
    §2  a real turn on the OpenRouter lane: the ring entry's `reported`
    §3  audit C-2: the modelUsage key the cost path asks for, hit or miss
    §4  scope: a Claude-lane turn is untouched (controls)

Anti-vacuity: `tests/_mutate_or_reported.py` breaks the shipped code nine
ways and requires a NAMED check here to go red for each.
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

from orgtree import openrouter, store, supervisor                # noqa: E402
from orgtree.ledger import USER                                  # noqa: E402

_LIVE_ROOT = os.path.normcase(os.path.abspath(
    os.path.join(os.path.expanduser("~"), "orgtree")))
assert os.path.normcase(os.path.abspath(store.DATA_ROOT)) != _LIVE_ROOT, \
    f"store.DATA_ROOT resolved to the LIVE root: {store.DATA_ROOT}"
assert os.path.normcase(os.path.abspath(store.DATA_ROOT)).startswith(
    os.path.normcase(os.path.abspath(H._TMP))), store.DATA_ROOT

PASS = 0
FAIL: list[tuple[str, str]] = []
VERBOSE = "-v" in sys.argv
ONLY = [a for a in sys.argv[1:] if not a.startswith("-")]

OR = "or-reported-fake"
OR_MODEL = "typed/fake"


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


# ══════════════════════════════════════════════════ the stand-in (steps)
#
# One stdout event per step, in order — the same idea as
# test_openrouter_typed_status's rig, with the reported metadata on the
# assistant events and `modelUsage` on the result. The field NAMES are the
# ones read out of the emitter: `message.{model,provider,id}` and a
# wrapper-level `request_id`.

STEPS_JS = r"""
'use strict'
const fs = require('fs'), os = require('os'), path = require('path')
const argv = process.argv.slice(2)
if (argv.includes('--version')) { console.log('9.9.9 (repcli)'); process.exit(0) }
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
say({ type: 'system', subtype: 'init', model: arg('--model') || 'fake', permissionMode: 'acceptEdits',
      cwd: process.cwd(), tools: [], mcp_servers: [] })
function serve(text) {
  try { fs.appendFileSync(process.env.SYNTHCLI_COUNT, JSON.stringify(text) + '\n') } catch (e) {}
  record({ type: 'user', message: { role: 'user', content: text } })
  for (const s of cfg.steps) {
    if (s.kind === 'assistant') {
      const msg = { role: 'assistant',
                    content: [{ type: 'text', text: s.text || 'ack.' }],
                    usage: { input_tokens: 1000, output_tokens: 10 } }
      if (s.model !== null && s.model !== undefined) msg.model = s.model
      if (s.provider !== undefined) msg.provider = s.provider
      if (s.id !== undefined) msg.id = s.id
      const ev = { type: 'assistant', message: msg }
      if (s.request_id !== undefined) ev.request_id = s.request_id
      if (s.sub) ev.parent_tool_use_id = 'tool-1'
      say(ev); record(ev)
    } else if (s.kind === 'engine_error') {
      const msg = { role: 'assistant', model: '<synthetic>', content: [{ type: 'text', text: s.text }],
                    provider: 'ENGINE-SHOULD-NOT-BE-REPORTED', id: 'engine-msg-1',
                    usage: { input_tokens: 0, output_tokens: 0 } }
      say({ type: 'assistant', message: msg, is_api_error_message: true,
            error: 'unknown', request_id: 'gen-engine-should-not-be-reported' })
    } else if (s.kind === 'result') {
      const res = { type: 'result', subtype: 'success', is_error: false, result: s.text || 'done',
                    usage: { input_tokens: 1000, output_tokens: 10 }, total_cost_usd: 0.0001 }
      if (s.model_usage !== undefined) res.modelUsage = s.model_usage
      say(res)
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


def msg(text: str = "ack.", *, model: str | None = OR_MODEL,
        provider: str | None = None, mid: str | None = None,
        request_id: str | None = None, sub: bool = False) -> dict[str, Any]:
    d: dict[str, Any] = {"kind": "assistant", "text": text}
    # `model=None` omits the field entirely — the "reported nothing" fixture
    d["model"] = model
    if provider is not None:
        d["provider"] = provider
    if mid is not None:
        d["id"] = mid
    if request_id is not None:
        d["request_id"] = request_id
    if sub:
        d["sub"] = True
    return d


def engine_error(text: str = "API Error: 402 no credits") -> dict[str, Any]:
    return {"kind": "engine_error", "text": text}


def result(text: str = "done",
           model_usage: dict[str, Any] | None = None) -> dict[str, Any]:
    d: dict[str, Any] = {"kind": "result", "text": text}
    if model_usage is not None:
        d["model_usage"] = model_usage
    return d


supervisor.notify = lambda slug, nid, event: None                # type: ignore[assignment]
supervisor.stream = lambda slug, nid, payload: None              # type: ignore[assignment]


def team(tier: str = OR) -> tuple[str, str, str]:
    org = store.create_org(f"zz orrep {time.time_ns()}")
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
    store.save_org(org)
    return org.d["slug"], boss, nid


def run(slug: str, nid: str, text: str = "hello") -> None:
    supervisor._run_one_turn(slug, nid, text)


def last_turn(slug: str, nid: str) -> dict[str, Any]:
    turns = store.load_org(slug).nodes[nid].get("turns") or []
    if not turns:
        raise AssertionError("the turn ring is empty — the turn was not booked")
    return dict(turns[-1])


# ══════════════════════════════════════════════════════════════════════ §1

def sec_unit() -> None:
    print("\n§1  the collector and the summary (pure)")
    N, S = supervisor._note_reported, supervisor._reported_summary

    def _allowlist():
        acc: dict[str, Any] = {}
        N(acc, {"request_id": "gen-1", "session_id": "SHOULD-NOT-BE-COPIED",
                "uuid": "nor-this"},
          {"model": "x-ai/grok-4.6", "provider": "xAI", "id": "msg_1",
           "usage": {"input_tokens": 5}, "content": [{"type": "text"}]})
        assert acc["rows"] == [{"model": "x-ai/grok-4.6", "provider": "xAI",
                                "id": "msg_1", "request_id": "gen-1"}], acc
        assert acc["requests"] == 1, acc
    check("collect · exactly the four allowlisted scalars, nothing else off the message",
          _allowlist)

    def _strings_only():
        acc: dict[str, Any] = {}
        N(acc, {"request_id": 7}, {"model": {"id": "x"}, "provider": None,
                                   "id": True})
        assert not acc, f"a non-string value was copied: {acc}"
        N(acc, {}, {"model": "   "})
        assert not acc, f"a blank string was copied: {acc}"
    check("collect · non-strings and blanks are not values — a message with none adds nothing",
          _strings_only)

    def _bounded():
        acc: dict[str, Any] = {}
        N(acc, {"request_id": "g" * 200}, {"model": "m" * 200,
                                           "provider": "p" * 200,
                                           "id": "i" * 200})
        row = acc["rows"][0]
        assert (len(row["model"]), len(row["provider"]), len(row["id"]),
                len(row["request_id"])) == (80, 40, 64, 64), row
    check("collect · every field is bounded, so one turn cannot grow the document",
          _bounded)

    def _cap():
        acc: dict[str, Any] = {}
        for i in range(supervisor._REPORTED_CAP + 5):
            N(acc, {}, {"model": f"m{i}"})
        assert len(acc["rows"]) == supervisor._REPORTED_CAP, len(acc["rows"])
        assert acc["requests"] == supervisor._REPORTED_CAP + 5, acc["requests"]
        out = S(acc)
        assert out and out["truncated"] is True, out
        assert out["requests"] == supervisor._REPORTED_CAP + 5, out
    check("collect · past the cap it stops storing, says truncated, and still counts every request",
          _cap)

    def _summary_distinct():
        acc: dict[str, Any] = {}
        N(acc, {"request_id": "gen-1"}, {"model": "b", "provider": "xAI", "id": "m1"})
        N(acc, {"request_id": "gen-2"}, {"model": "b", "provider": "xAI", "id": "m2"})
        out = S(acc)
        assert out == {"requests": 2, "models": ["b"], "providers": ["xAI"],
                       "mixed": False, "first_id": "m1",
                       "first_request_id": "gen-1"}, out
        # …and the set really is a SET, not the last value kept twice
        N(acc, {}, {"model": "a", "provider": "xAI"})
        out2 = S(acc)
        assert out2 and out2["models"] == ["a", "b"], out2
    check("summary · every distinct value, sorted; first id and first upstream id are the FIRST",
          _summary_distinct)

    def _summary_mixed():
        acc: dict[str, Any] = {}
        N(acc, {}, {"model": "m", "provider": "xAI"})
        N(acc, {}, {"model": "m", "provider": "Together"})
        out = S(acc)
        assert out and out["mixed"] is True, out
        assert out["providers"] == ["Together", "xAI"], out
        acc2: dict[str, Any] = {}
        N(acc2, {}, {"model": "m1", "provider": "xAI"})
        N(acc2, {}, {"model": "m2", "provider": "xAI"})
        out2 = S(acc2)
        assert out2 and out2["mixed"] is True, out2
    check("summary · a turn served by more than one reported model OR provider says mixed",
          _summary_mixed)

    def _empty_is_none():
        assert S({}) is None and S({"rows": []}) is None, "an empty turn invented a summary"
    check("summary · a turn that reported nothing has no summary at all (not an empty one)",
          _empty_is_none)


# ══════════════════════════════════════════════════════════════════════ §2

def sec_turn() -> None:
    print("\n§2  a real turn on the OpenRouter lane")
    undo = H._stub_login()
    try:
        _sec_turn_body()
    finally:
        undo()


def _sec_turn_body() -> None:
    slug, boss, nid = team()
    steps(msg("first", provider="xAI", mid="msg_1", request_id="gen-aaa"),
          msg("second", provider="xAI", mid="msg_2", request_id="gen-bbb"),
          result("second"))
    run(slug, nid)
    rep = last_turn(slug, nid).get("reported")

    def _on_the_ring():
        assert isinstance(rep, dict), f"the turn booked no reported block: {last_turn(slug, nid)}"
        assert rep["requests"] == 2, rep
        assert rep["providers"] == ["xAI"] and rep["models"] == [OR_MODEL], rep
        assert rep["mixed"] is False, rep
        assert rep["first_id"] == "msg_1", rep
        assert rep["first_request_id"] == "gen-aaa", (
            "the upstream generation id from the FIRST message was not kept")
    check("turn · the ring entry carries the reported model, provider and upstream id",
          _on_the_ring)

    slug, boss, nid = team()
    steps(msg("a", provider="xAI", mid="m1", request_id="gen-1"),
          msg("b", model="other/model", provider="Together", mid="m2"),
          result("b"))
    run(slug, nid)

    def _mixed_turn():
        rep2 = last_turn(slug, nid).get("reported")
        assert isinstance(rep2, dict), rep2
        assert rep2["mixed"] is True, rep2
        assert rep2["providers"] == ["Together", "xAI"], rep2
        assert sorted(rep2["models"]) == sorted([OR_MODEL, "other/model"]), rep2
    check("turn · a turn answered by two different reported upstreams says so, and keeps both",
          _mixed_turn)

    slug, boss, nid = team()
    steps(engine_error(), msg("recovered", provider="xAI", mid="m1",
                              request_id="gen-real"),
          result("recovered"))
    run(slug, nid)

    def _engine_excluded():
        rep3 = last_turn(slug, nid).get("reported") or {}
        blob = json.dumps(rep3)
        assert "ENGINE-SHOULD-NOT-BE-REPORTED" not in blob, (
            f"an ENGINE-AUTHORED record was reported as an upstream: {rep3}")
        assert "engine-msg-1" not in blob and "gen-engine" not in blob, rep3
        assert rep3.get("requests") == 1 and rep3.get("providers") == ["xAI"], rep3
    check("turn · an engine-authored error record is NOT an upstream and is never reported",
          _engine_excluded)

    slug, boss, nid = team()
    steps(msg("mine", provider="xAI", mid="m1"),
          msg("subagent", provider="SUBAGENT-NOT-MINE", mid="m2", sub=True),
          result("mine"))
    run(slug, nid)

    def _subagent_excluded():
        rep4 = last_turn(slug, nid).get("reported") or {}
        assert "SUBAGENT-NOT-MINE" not in json.dumps(rep4), (
            f"a subagent's message was counted as this agent's upstream: {rep4}")
        assert rep4.get("requests") == 1, rep4
    check("turn · a subagent's message is not this agent's upstream", _subagent_excluded)

    slug, boss, nid = team()
    steps(msg("no metadata at all", model=None), result("no metadata at all"))
    run(slug, nid)

    def _nothing_reported():
        assert "reported" not in last_turn(slug, nid), (
            "a turn whose messages reported nothing still got a block")
    check("turn · a turn whose messages carry no metadata gets no block (absent, not empty)",
          _nothing_reported)


# ══════════════════════════════════════════════════════════════════════ §3

def sec_model_usage_key() -> None:
    print("\n§3  audit C-2 — the modelUsage key the cost path asks for")
    undo = H._stub_login()
    try:
        _sec_mu_body()
    finally:
        undo()


def _sec_mu_body() -> None:
    slug, boss, nid = team()
    steps(msg("x", provider="xAI"),
          result("x", model_usage={"openrouter/typed/fake": {
              "costUSD": 0.001, "costBasis": "list"}}))
    run(slug, nid)

    def _miss_recorded():
        got = last_turn(slug, nid).get("model_usage_key")
        assert isinstance(got, dict), (
            f"the cost path's lookup key was not recorded at all: {last_turn(slug, nid)}")
        assert got["asked"] == OR_MODEL, got
        assert got["matched"] is False, (
            f"a key the CLI did NOT write was recorded as a hit: {got}")
        assert got["keys"] == ["openrouter/typed/fake"], got
    check("C-2 · the CLI keying modelUsage differently is recorded as a MISS, with its own keys",
          _miss_recorded)

    slug, boss, nid = team()
    steps(msg("x", provider="xAI"),
          result("x", model_usage={OR_MODEL: {"costUSD": 0.001,
                                              "costBasis": "list"}}))
    run(slug, nid)

    def _hit_recorded():
        got = last_turn(slug, nid).get("model_usage_key")
        assert isinstance(got, dict) and got["matched"] is True, got
        assert got["asked"] == OR_MODEL, got
    check("C-2 · a key that does match is recorded as a hit", _hit_recorded)

    slug, boss, nid = team()
    _long = "k" * 300
    steps(msg("x", provider="xAI"),
          result("x", model_usage={f"{_long}{i}": {"costUSD": 0.0}
                                   for i in range(9)}))
    run(slug, nid)

    def _keys_bounded():
        got = last_turn(slug, nid).get("model_usage_key") or {}
        keys = got.get("keys") or []
        assert len(keys) == 4, f"the key COUNT is unbounded: {len(keys)}"
        assert all(len(k) == 80 for k in keys), (
            f"a key the CLI chose went into the document at full length: "
            f"{[len(k) for k in keys]}")
    check("C-2 · the CLI's own keys are bounded by count AND by length", _keys_bounded)

    def _not_a_served_model():
        got = last_turn(slug, nid).get("model_usage_key") or {}
        assert "served" not in json.dumps(got).lower(), (
            "the C-2 record calls something 'served' — it is a lookup key, "
            "not evidence about which machine answered")
    check("C-2 · it is recorded as a lookup key, never as a served model", _not_a_served_model)


# ══════════════════════════════════════════════════════════════════════ §4

def sec_scope() -> None:
    print("\n§4  scope — a Claude-lane turn is untouched")
    undo = H._stub_login()
    try:
        _sec_scope_body()
    finally:
        undo()


def _sec_scope_body() -> None:
    slug, boss, nid = team("haiku")
    steps(msg("x", model="claude-haiku", provider="Anthropic", mid="m1",
              request_id="req-1"),
          result("x", model_usage={"claude-haiku": {"costUSD": 0.001}}))
    run(slug, nid)

    def _claude_untouched():
        entry = last_turn(slug, nid)
        assert "reported" not in entry, (
            f"the OpenRouter-only capture reached a CLAUDE turn: {entry}")
        assert "model_usage_key" not in entry, (
            f"the C-2 record reached a CLAUDE turn: {entry}")
        assert entry.get("cost") is not None, "…and the ordinary entry stopped being written"
    check("scope · a haiku turn books its ring entry with neither block", _claude_untouched)


def main() -> None:
    openrouter.set_key("or-test-key-000000")
    try:
        sec_unit()
        sec_turn()
        sec_model_usage_key()
        sec_scope()
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
