"""VERIFIED HANDOFF RECORD — the production module and its publication door.

    python backend/tests/test_handoff_record.py     (plain asserts)

Audit §3 / reset-action-plan item 15; contract in
`mail-ack-contract/handoff-contract.md` v2 (Astra-approved 2026-09-05,
staged-directory publication). The prototype probe
(`evidence/handoff/probe_handoff_contract.py`) proved the contract against a
STAND-IN module. This suite drives the SHIPPED code: `orgtree.handoff` and
the real `supervisor.export_predecessor_transcript`, through a real
`ledger.switch_model` crossing in a throwaway data root.

What each section establishes:

  §1 a record built from a fixture containing every excluded thing (private
     reasoning, a signature, a sidechain row, a compaction summary, an
     outside-grant write, an orphan tool result, a machine envelope) verifies
     ANCHORED and leaks none of them; the build is deterministic.
  §2 ARTIFACT FORGERIES are each rejected, and the two DECLARED
     LIMITS of the contract (a consistent forgery of a captured view row or
     seat passes without its anchor) are asserted as limits, not as passes.
  §3 a zero-omission input verifies clean — the anti-vacuity control for §1:
     the omission machinery can report nothing AND can still be caught lying.
  §4 escaping: quotes, backslashes, tabs, newlines, unicode round-trip; a
     re-escaped quotation is caught.
  §5 PUBLICATION: staged directory + one rename. A failing verify, a failing
     render or a failing rename leaves NOTHING under the published name and
     no staging directory behind; the same generation is never overwritten;
     `read_generation` refuses a manifest-less, corrupted or truncated
     directory.
  §6 the REAL DOOR: a real cross-provider `switch_model` +
     `export_predecessor_transcript` publishes `handoff-g<gen>/`, and that
     record verifies against the live sidecar rows, node doc and mailbox.
  §7 all seven boundary call sites go through that one function (source
     scan, with the count asserted so a new door cannot be added silently).
  §8 FLAG-OFF COMPATIBILITY, measured against `main` itself: with
     `handoff.flag` absent the identity prompt is BYTE-IDENTICAL to the one
     main's own supervisor.py builds for the same node, and the boundary
     adds no notice. Positive control: with the flag on, the same comparison
     reports a difference.
  §9 generation-specific lookup: the successor reads g<generation-1> only; a
     stale older generation and a corrupted current one are not spliced.
  §10 the boundary is best-effort: a record that cannot be built or does not
     verify leaves the transcript copy and the split intact.
  §11 TWELVE MUTANTS of the shipped code, each asserted to turn a NAMED check
     of this suite red (a check that cannot fail is not a check).

Review round 2 (Astra, 2026-09-05) added, in §1 and §5: every private form any
lane actually persists — including the codex leg's reasoning item, which its
writer stores in Claude's own `thinking` shape with `signature: "codex"` — and
the block/row forms this code does not know, which are admitted by NOTHING and
counted by type name; a TRUNCATED leak (300 chars of a thinking block) that
whole-string containment cannot see, with a false-positive control so that
reasoning quoting the user verbatim does not make a record unpublishable;
eleven malformed-but-valid-JSON manifests that must answer "no record" rather
than raise on the identity-prompt path; and the boundary reason, which is the
door's own rather than a guess from tier equality.

Hermetic: own throwaway `ORGTREE_DATA` asserted not to be the live root, no
backend process, no network, no provider call, no write outside the temp
dirs and this repository's worktree.
"""

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import traceback

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(HERE, ".."))

DATA = tempfile.mkdtemp(prefix="orgtree-handoff-")
os.environ["ORGTREE_DATA"] = DATA
os.environ["ORGTREE_WARM"] = "0"
os.environ["ORGTREE_PORT"] = "9"          # nothing must ever reach a backend
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

from orgtree import handoff, store, supervisor                      # noqa: E402
from orgtree.ledger import USER                                     # noqa: E402

assert os.path.realpath(store.DATA_ROOT) == os.path.realpath(DATA), store.DATA_ROOT
LIVE = os.path.realpath(os.path.expanduser("~/orgtree"))
assert os.path.realpath(store.DATA_ROOT) != LIVE, "bound to the LIVE root"

PASS = 0
FAIL: list[tuple[str, str]] = []
OBS: dict = {}


def check(label, fn):
    global PASS
    try:
        fn()
    except Exception:                                            # noqa: BLE001
        FAIL.append((label, traceback.format_exc()))
        print(f"  FAIL     {label}")
        return
    PASS += 1
    print(f"  ok {PASS:2d}  {label}")


# ── mutants: prove each contract line of this suite can fail ───────────────
# Every mutant is a plausible weakening of the SHIPPED code, applied here in
# the test process (never in the module). The parent run in §11 re-runs this
# whole file with HANDOFF_MUTANT set and asserts the NAMED check goes red.
MUTANT = os.environ.get("HANDOFF_MUTANT", "")
_real_extract = handoff.extract

if MUTANT == "keep_thinking":
    # the extractor stops dropping thinking blocks (verify rebuilds with the
    # SAME broken extractor, so only the intrinsic I1 can catch it)
    def _keep(inputs, lines):
        rec = _real_extract(inputs, lines)
        for s in handoff.private_strings(lines):
            rec["predecessor_said"].append(
                {"role": "assistant", "text": s, "truncated": False,
                 "ref": rec["tool_pairs"][0]["ref"] if rec["tool_pairs"] else {}})
            break
        return rec
    handoff.extract = _keep
elif MUTANT == "elevate_role":
    def _role(inputs, lines):
        rec = _real_extract(inputs, lines)
        for it in rec["predecessor_said"]:
            it["role"] = "user"          # a claim relabelled as an instruction
        return rec
    handoff.extract = _role
elif MUTANT == "leak_outside_grant":
    def _leak(inputs, lines):
        rec = _real_extract(inputs, lines)
        for p in rec["tool_pairs"]:
            if "path" in p and p["path"] is None:
                p["path"] = "/outside/leaked.txt"
        return rec
    handoff.extract = _leak
elif MUTANT == "claim_continuity":
    def _cont(inputs, lines):
        rec = _real_extract(inputs, lines)
        rec["continuity"]["provider_context"] = "carried"
        return rec
    handoff.extract = _cont
elif MUTANT == "pair_by_id_only":
    # the pre-fix pairing: pairs keyed by tool_use id alone, so a reused id
    # renders as N copies of its LAST occurrence
    def _byid(inputs, lines):
        rec = _real_extract(inputs, lines)
        last = {p["id"]: p for p in rec["tool_pairs"]}
        rec["tool_pairs"] = [last[p["id"]] for p in rec["tool_pairs"]]
        return rec
    handoff.extract = _byid
elif MUTANT == "truncated_thinking":
    # the extractor quotes the FIRST 300 CHARS of each thinking block: whole
    # string containment cannot see it, the fragment scan must
    def _trunc(inputs, lines):
        rec = _real_extract(inputs, lines)
        for t in handoff.private_strings(lines):
            if len(t) >= 300:
                rec["predecessor_said"].append(
                    {"role": "assistant", "text": t[:300], "truncated": True,
                     "ref": (rec["predecessor_said"] or [{"ref": {}}])[0]["ref"]})
                break
        return rec
    handoff.extract = _trunc
elif MUTANT == "admit_unknown_blocks":
    # unknown assistant blocks are treated as text instead of being dropped
    def _admit(inputs, lines):
        rec = _real_extract(inputs, lines)
        for line in lines:
            r = json.loads(line) if line.strip().startswith("{") else {}
            m = r.get("message") if isinstance(r.get("message"), dict) else {}
            for b in m.get("content") or []:
                if isinstance(b, dict) and b.get("type") == "redacted_thinking":
                    rec["predecessor_said"].append(
                        {"role": "assistant", "text": str(b.get("data")),
                         "truncated": False,
                         "ref": (rec["predecessor_said"] or [{"ref": {}}])[0]["ref"]})
        return rec
    handoff.extract = _admit
elif MUTANT == "reader_trusts_entry_shapes":
    # the pre-fix reader: `files` is a dict, so its entries must be dicts too
    _real_read = handoff.read_generation

    def _trusting(scratch, gen, node=None):
        d = handoff.generation_dir(scratch, gen)
        try:
            with open(os.path.join(d, handoff.MANIFEST), encoding="utf-8") as f:
                man = json.load(f)
            files = man.get("files") if isinstance(man, dict) else None
            if not isinstance(files, dict) or man.get("kind") != handoff.KIND:
                return None
            out = {"dir": d, "generation": gen}
            for name in (handoff.RECORD_JSON, handoff.RECORD_MD):
                with open(os.path.join(d, name), "rb") as f:
                    b = f.read()
                want = files.get(name) or {}
                if handoff.sha256(b) != want.get("sha256"):
                    return None
                out[name] = b.decode("utf-8")
            out["record"] = json.loads(out[handoff.RECORD_JSON])
            return out
        except (OSError, ValueError):
            return None
    handoff.read_generation = _trusting
elif MUTANT == "memo_key_claimed_hash":
    # the shipped mistake: the memo keyed on the hash the ARTIFACT CLAIMS
    # instead of the hash of the bytes in hand
    _real_core_h = handoff._verify_core
    _claim_cache: dict = {}

    def _claimed(art, lines):
        try:
            k = (handoff.sha256(json.dumps(art, ensure_ascii=False)),
                 str(((art.get("inputs") or {}).get("transcript") or {}).get("sha256")),
                 len(lines))
        except Exception:                                        # noqa: BLE001
            return _real_core_h(art, lines)
        if k not in _claim_cache:
            _claim_cache[k] = _real_core_h(art, lines)
        return _claim_cache[k]
    handoff._verify_core = _claimed
elif MUTANT == "memo_key_transcript_only":
    # the verify memo keyed on the TRANSCRIPT alone: a forged artifact over the
    # same transcript would be answered from the cache
    _real_core = handoff._verify_core
    _weak_cache: dict = {}

    def _weak(art, lines):
        k = ((art.get("inputs") or {}).get("transcript") or {}).get("sha256")
        if k not in _weak_cache:
            _weak_cache[k] = _real_core(art, lines)
        return _weak_cache[k]
    handoff._verify_core = _weak
elif MUTANT == "reason_guessed":
    # the pre-fix publisher: the boundary reason inferred from tier equality
    _real_capture = handoff.capture

    def _guess(**kw):
        b = dict(kw.get("boundary") or {})
        b["reason"] = ("switch_model"
                       if (b.get("from") or {}).get("tier") != (b.get("to") or {}).get("tier")
                       else "cheap_compact")
        kw["boundary"] = b
        return _real_capture(**kw)
    handoff.capture = _guess
elif MUTANT == "nonatomic_publish":
    # publish IN PLACE instead of staging + one rename
    def _nonatomic(scratch, gen, art, lines):
        dst = handoff.generation_dir(scratch, gen)
        if os.path.exists(dst):
            return None
        try:
            os.makedirs(dst)
            with open(os.path.join(dst, handoff.RECORD_JSON), "w", encoding="utf-8") as f:
                f.write(json.dumps(art, indent=1, ensure_ascii=False))
            md = handoff.render_md(art)                # may raise, mid-publish
            with open(os.path.join(dst, handoff.RECORD_MD), "w", encoding="utf-8") as f:
                f.write(md)
            return dst
        except Exception:                                        # noqa: BLE001
            return None
    handoff.write_generation = _nonatomic
elif MUTANT == "read_ignores_manifest":
    # the reader trusts the directory instead of its manifest
    def _loose(scratch, gen, node=None):
        d = handoff.generation_dir(scratch, gen)
        try:
            out = {"dir": d, "generation": gen}
            for name in (handoff.RECORD_JSON, handoff.RECORD_MD):
                with open(os.path.join(d, name), encoding="utf-8") as f:
                    out[name] = f.read()
            out["record"] = json.loads(out[handoff.RECORD_JSON])
            return out
        except (OSError, ValueError):
            return None
    handoff.read_generation = _loose
elif MUTANT == "flag_ignored":
    supervisor.handoff_flag_on = lambda: True

# ═══════════════════════════════════════════════════════════════════════════
# fixture: one transcript holding every excluded thing
THINK = "PRIVATE-REASONING-TEXT never carry me"
SIG = "SIGNATURE-BYTES-0xdeadbeef-0123456789"
OUTSIDE = os.path.join(tempfile.gettempdir(), "outside-grant-secret.txt")
GRANT = tempfile.mkdtemp(prefix="handoff-grant-")
INSIDE = os.path.join(GRANT, "widget.txt")
ENVELOPE = ("[ORG STATE #3 — current]\nsecret chart\n[END ORG STATE]\n\n"
            "[MAIL — 1 message]\nFROM boss · request\nUSER-INSTRUCTION-2 ship by nine\n[END MAIL]")
VISIBLE = "FROM boss · request\nUSER-INSTRUCTION-2 ship by nine"
TRICKY = ('USER-INSTRUCTION-3 "quoted"\\path\\x\tTAB\nline2 literal \\n here '
          '— ünïcödé ✓ {"json": [1]}')


# Ground truth for the forms that must NEVER be admitted. Each is a distinct
# token so its presence anywhere in the record, the render or the prompt is
# unambiguous — and each has a row in the fixture below, so the checks that
# look for them cannot pass vacuously.
CODEX_THINK = ("CODEX-REASONING-SUMMARY the codex leg persists its reasoning item "
               "in Claude's own thinking shape, so it is private the same way and "
               "this sentence is long enough to be a fragment leak on its own")
REDACTED = "REDACTED-THINKING-PAYLOAD-0xfeed"
UNKNOWN_BLOCK_TEXT = "SERVER-TOOL-QUERY never admitted"
UNKNOWN_USER_BLOCK = "DOCUMENT-BLOCK-PAYLOAD never admitted"
ATTACH_TEXT = "ATTACHMENT-ROW-TEXT not a user turn"
QUEUE_TEXT = "QUEUE-OPERATION-TEXT not a user turn"
LASTPROMPT_TEXT = "LAST-PROMPT-ROW-TEXT not a user turn"
ATIS_TEXT = "ATIS-LATCH-TEXT not a user turn"


def fixture_records() -> list[dict]:
    with open(INSIDE, "w", encoding="utf-8") as f:
        f.write("widget v1\n")
    return [
        {"type": "user", "timestamp": "2026-09-05T00:00:01Z", "uuid": "u1",
         "message": {"role": "user",
                     "content": "USER-INSTRUCTION-1 build the widget, never deploy"}},
        {"type": "assistant", "timestamp": "2026-09-05T00:00:02Z", "uuid": "a1",
         "message": {"id": "m1", "role": "assistant", "model": "x", "content": [
             {"type": "thinking", "thinking": THINK, "signature": SIG},
             {"type": "tool_use", "id": "t1", "name": "Write",
              "input": {"file_path": INSIDE, "content": "widget v1\n"}}]}},
        {"type": "user", "timestamp": "2026-09-05T00:00:03Z", "uuid": "u2",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "t1", "content": "wrote " + INSIDE}]}},
        {"type": "assistant", "timestamp": "2026-09-05T00:00:04Z", "uuid": "a2",
         "message": {"id": "m2", "role": "assistant", "model": "x", "content": [
             {"type": "tool_use", "id": "t2", "name": "Write",
              "input": {"file_path": OUTSIDE, "content": "leak"}}]}},
        {"type": "user", "timestamp": "2026-09-05T00:00:05Z", "uuid": "u3",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "t2", "content": "ok"},
             {"type": "tool_result", "tool_use_id": "t-orphan", "content": "ORPHAN-RESULT"}]}},
        {"type": "assistant", "timestamp": "2026-09-05T00:00:06Z", "uuid": "a3",
         "message": {"id": "m3", "role": "assistant", "model": "x", "content": [
             {"type": "tool_use", "id": "t3", "name": "mcp__orgtree__orgtree_status",
              "input": {"status": "working", "summary": "STATUS-CLAIM widget half built"}}]}},
        {"type": "user", "timestamp": "2026-09-05T00:00:07Z", "uuid": "u4",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "t3", "content": '{"recorded":"working"}'}]}},
        {"type": "user", "timestamp": "2026-09-05T00:00:08Z", "uuid": "u5",
         "message": {"role": "user", "content": [{"type": "text", "text": ENVELOPE},
                                                 {"type": "image", "source": {"data": "AAAA"}}]}},
        {"type": "user", "timestamp": "2026-09-05T00:00:09Z", "uuid": "u6",
         "message": {"role": "user",
                     "content": "[ORG STATE #4]\nUNPROJECTED-ENVELOPE\n[END ORG STATE]"}},
        {"type": "user", "timestamp": "2026-09-05T00:00:09Z", "uuid": "u6b",
         "message": {"role": "user", "content": TRICKY}},
        {"type": "system", "subtype": "compact_boundary", "timestamp": "2026-09-05T00:00:10Z",
         "compactMetadata": {"preTokens": 120000}},
        {"type": "user", "timestamp": "2026-09-05T00:00:11Z", "uuid": "u7",
         "isCompactSummary": True,
         "message": {"role": "user", "content": "COMPACTION-INTERNAL summary text"}},
        {"type": "assistant", "timestamp": "2026-09-05T00:00:12Z", "uuid": "a4",
         "isSidechain": True,
         "message": {"id": "m4", "role": "assistant", "model": "x", "content": [
             {"type": "text", "text": "SIDECHAIN-TEXT"}]}},
        {"type": "assistant", "timestamp": "2026-09-05T00:00:13Z", "uuid": "a5",
         "message": {"id": "m5", "role": "assistant", "model": "<synthetic>",
                     "content": "API error"}},
        {"type": "assistant", "timestamp": "2026-09-05T00:00:14Z", "uuid": "a6",
         "message": {"id": "m6", "role": "assistant", "model": "x", "content": [
             {"type": "tool_use", "id": "t4", "name": "Bash",
              "input": {"command": "git log --oneline -1"}}]}},
        {"type": "assistant", "timestamp": "2026-09-05T00:00:15Z", "uuid": "a7",
         "message": {"id": "m7", "role": "assistant", "model": "x", "content": [
             {"type": "text", "text": "ASSISTANT-FINAL widget built; deploy is yours"}]}},
        # a REUSED tool_use id (a resumed or concatenated session does this):
        # its own pair, its own result — never a second copy of the first call
        {"type": "assistant", "timestamp": "2026-09-05T00:00:16Z", "uuid": "a8",
         "message": {"id": "m8", "role": "assistant", "model": "x", "content": [
             {"type": "tool_use", "id": "t1", "name": "Bash",
              "input": {"command": "echo REUSED-ID"}}]}},
        {"type": "user", "timestamp": "2026-09-05T00:00:17Z", "uuid": "u8",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "t1", "content": "REUSED-ID result"}]}},
        # the CODEX lane's reasoning, in the shape its writer actually
        # persists (supervisor.py ~10750): Claude's block with signature
        # "codex" — a positive control for the non-Claude private form
        {"type": "assistant", "timestamp": "2026-09-05T00:00:18Z", "uuid": "a9",
         "message": {"id": "codex-think-1", "role": "assistant", "model": "gpt-5.6",
                     "content": [{"type": "thinking", "thinking": CODEX_THINK,
                                  "signature": "codex"}]}},
        # block forms this code does not know: admitted by nothing, counted by
        # type name (a future reasoning form arrives exactly like this)
        {"type": "assistant", "timestamp": "2026-09-05T00:00:19Z", "uuid": "a10",
         "message": {"id": "m10", "role": "assistant", "model": "x", "content": [
             {"type": "redacted_thinking", "data": REDACTED},
             {"type": "server_tool_use", "id": "srv1", "name": "web_search",
              "input": {"query": UNKNOWN_BLOCK_TEXT}},
             "not-a-block"]}},
        {"type": "user", "timestamp": "2026-09-05T00:00:20Z", "uuid": "u9",
         "message": {"role": "user", "content": [
             {"type": "document", "source": {"data": UNKNOWN_USER_BLOCK}}]}},
        # row types a real CLI 2.1.258 transcript carries beside user/assistant
        {"type": "attachment", "timestamp": "2026-09-05T00:00:21Z", "uuid": "x1",
         "attachment": {"type": "hook_additional_context", "text": ATTACH_TEXT}},
        {"type": "queue-operation", "timestamp": "2026-09-05T00:00:22Z", "uuid": "x2",
         "operation": {"text": QUEUE_TEXT}},
        {"type": "last-prompt", "timestamp": "2026-09-05T00:00:23Z", "uuid": "x3",
         "lastPrompt": LASTPROMPT_TEXT},
        {"type": "mode", "timestamp": "2026-09-05T00:00:24Z", "uuid": "x4",
         "mode": "acceptEdits"},
        {"type": "atis-latch", "timestamp": "2026-09-05T00:00:25Z", "uuid": "x5",
         "atis": {"note": ATIS_TEXT}},
    ]


RECS = fixture_records()
LINES = [json.dumps(r, ensure_ascii=False) + "\n" for r in RECS]
VIEWS = {handoff.sha256(ENVELOPE): VISIBLE}
NODE = {"charter": "build widgets", "team_charter": "", "parent": "boss", "grant": 4,
        "scope": {"add_dirs": [{"path": GRANT}], "tools": {"bash": True}},
        "last_status": {"status": "working", "summary": "STATUS-CLAIM widget half built"}}
MAIL = [{"id": "m1", "from": "boss", "kind": "request", "at": "2026-09-05T00:00:20Z",
         "body": "PENDING-MAIL please also test it"}]
ASK = {"id": "q1", "question": "MOOTED-ASK which colour?", "status": "moot",
       "reason": "the asking session was replaced by a cross-provider model switch"}
BOUNDARY = {"reason": "switch_model", "from": {"tier": "luna", "provider": "openai"},
            "to": {"tier": "opus", "provider": "claude"}, "at": "2026-09-05T00:01:00Z"}
EXCLUDED = [THINK, SIG, "SIDECHAIN-TEXT", "COMPACTION-INTERNAL", "secret chart",
            "UNPROJECTED-ENVELOPE", "outside-grant-secret", "ORPHAN-RESULT",
            CODEX_THINK, REDACTED, UNKNOWN_BLOCK_TEXT, UNKNOWN_USER_BLOCK,
            ATTACH_TEXT, QUEUE_TEXT, LASTPROMPT_TEXT, ATIS_TEXT]


def build(lines=None, views=None):
    return handoff.capture(nid="a1", node=NODE, lines=lines or LINES,
                           views_all=VIEWS if views is None else views,
                           mailbox=MAIL, mooted_ask=ASK, grants=[GRANT],
                           boundary=BOUNDARY, at="2026-09-05T00:01:00Z")


def expect_reject(label, mutate, *, anchors=None):
    def fn():
        art = build()
        mutate(art)
        bad = handoff.verify(art, LINES, **(anchors or {}))
        assert bad, "forgery NOT detected"
        OBS.setdefault("forgeries", {})[label] = bad[:3]
    check(f"forgery rejected: {label}", fn)


# ── §1 clean build ─────────────────────────────────────────────────────────
print(f"\ndata root {DATA}")
print("\n§1 clean build from a fixture holding every excluded thing")
ART = build()


def t1():
    blob = json.dumps(ART, ensure_ascii=False)
    raw = "".join(LINES)
    for s in EXCLUDED:
        assert s in raw, f"control: fixture lacks {s!r}"
        assert s not in blob, f"leaked into the record: {s!r}"
    bad = handoff.verify(ART, LINES, views_all=VIEWS, seat=NODE, mailbox=MAIL)
    assert bad == [], bad
    rec = ART["record"]
    texts = [i["text"] for i in rec["instructions_received"]]
    assert texts[0].startswith("USER-INSTRUCTION-1") and VISIBLE in texts and TRICKY in texts
    pj = [i for i in rec["instructions_received"] if i.get("projected")]
    assert len(pj) == 1 and pj[0]["projected"]["raw_sha256"] == handoff.sha256(ENVELOPE)
    assert ART["inputs"]["views"] == VIEWS, "only the used view row is captured"
    byid: dict = {}
    for p in rec["tool_pairs"]:
        byid.setdefault(p["id"], []).append(p)
    assert byid["t1"][0]["paired"] and byid["t2"][0]["path"] is None
    assert not byid["t4"][0]["paired"]
    om = {o["kind"]: o for o in rec["omissions"]}
    assert om["orphan_tool_result"]["ids"] == ["t-orphan"]
    assert om["unpaired_tool_use"]["ids"] == ["t4"]
    assert rec["artifacts"][0]["disk"]["exists"] and rec["artifacts"][0]["path"] == INSIDE
    assert rec["continuity"]["provider_context"] == "none"
    assert build() == ART, "not deterministic"
    md = handoff.render_md(ART)
    for s in EXCLUDED:
        assert s not in md, f"leaked into the rendered file: {s!r}"
    assert "NOT path-filtered" in md
    OBS["omissions"] = rec["omissions"]
    OBS["anchors_line"] = handoff.anchors_ran(views_all=VIEWS, seat=NODE, mailbox=MAIL)


check("excluded content absent from record AND render; anchored verify == []; deterministic", t1)


def t1b():
    assert "WITHOUT anchors" in handoff.anchors_ran()
    assert handoff.anchors_ran(seat=NODE) == "anchored: seat"


check("anchors_ran states what a given verify call could not check", t1b)


def t1_repeated_ids():
    """A transcript may reuse a tool_use id. Each occurrence must stay its own
    pair with its own result — and the independent recount must agree, or no
    such transcript could ever produce a publishable record."""
    t1s = [p for p in ART["record"]["tool_pairs"] if p["id"] == "t1"]
    assert [p["name"] for p in t1s] == ["Write", "Bash"], t1s
    assert all(p["paired"] for p in t1s), t1s
    assert t1s[0]["result"]["excerpt"].startswith("wrote ")
    assert "REUSED-ID result" in t1s[1]["result"]["excerpt"]
    assert handoff.verify(ART, LINES) == [], handoff.verify(ART, LINES)


check("repeated tool_use id: each occurrence is its own pair, results attach in order",
      t1_repeated_ids)


def t1_private_forms():
    """Every PRIVATE form any lane actually persists is excluded, and the
    exclusion is the whitelist, not the scan. Read in the writers 2026-09-05:
    the codex leg persists a reasoning item as Claude's own
    {"type": "thinking", …, "signature": "codex"} (supervisor.py ~10750); the
    antigravity and openrouter legs persist only text / tool_use / tool_result
    (~12078, ~12152, ~12172, ~11100); journalled user rows carry only text,
    image and tool_result (~6182). The Claude lane's file is written by the
    CLI, so its forms are OBSERVED instead: a real 441-row CLI 2.1.258
    transcript held user/assistant/attachment/queue-operation/last-prompt/
    mode/atis-latch rows and text/thinking/tool_use/tool_result blocks only."""
    rec = ART["record"]
    om = {o["kind"]: o["count"] for o in rec["omissions"]}
    # the codex reasoning row is counted as a thinking block like any other
    assert om["thinking_block"] == 2 and om["signature"] == 2, om
    # forms this code does not know are counted BY TYPE NAME and admitted nowhere
    for kind in ("unrecognized_assistant_block:redacted_thinking",
                 "unrecognized_assistant_block:server_tool_use",
                 "unrecognized_assistant_block:not-a-block",
                 "unrecognized_user_block:document"):
        assert om.get(kind) == 1, (kind, om)
    for t in ("attachment", "queue-operation", "last-prompt", "mode", "atis-latch"):
        assert om.get("skipped_row:" + t) == 1, (t, om)
    # nothing from any of them reached the record, the render or the pairs
    blob = json.dumps(ART, ensure_ascii=False) + handoff.render_md(ART)
    for tok in (CODEX_THINK, REDACTED, UNKNOWN_BLOCK_TEXT, UNKNOWN_USER_BLOCK,
                ATTACH_TEXT, QUEUE_TEXT, LASTPROMPT_TEXT, ATIS_TEXT):
        assert tok in "".join(LINES), f"control: fixture lacks {tok!r}"
        assert tok not in blob, f"leaked: {tok!r}"
    assert not any(p["name"] == "web_search" for p in rec["tool_pairs"]),         "a server_tool_use block became a tool pair"


check("every private/unknown block form any lane persists is excluded by the "
      "whitelist and counted by type name", t1_private_forms)


def t1_truncated_leak():
    """The scan half of I1 must see a leak that whole-string containment
    cannot: the FIRST 300 CHARS of a thinking block, quoted as assistant
    text. The first assertion is the point — plain containment says nothing —
    and the second is what `leak_fragments` adds."""
    long_think = (THINK + " ") * 40
    lines = list(LINES)
    lines.insert(1, json.dumps(
        {"type": "assistant", "timestamp": "2026-09-05T00:00:02Z", "uuid": "aL",
         "message": {"id": "mL", "role": "assistant", "model": "x", "content": [
             {"type": "thinking", "thinking": long_think}]}}) + "\n")
    art = handoff.capture(nid="a1", node=NODE, lines=lines, views_all=VIEWS,
                          mailbox=MAIL, mooted_ask=ASK, grants=[GRANT],
                          boundary=BOUNDARY, at="2026-09-05T00:01:00Z")
    assert handoff.verify(art, lines) == [], handoff.verify(art, lines)
    fragment = long_think[:300]
    art["record"]["predecessor_said"].append(
        {"role": "assistant", "text": fragment, "truncated": True,
         "ref": art["record"]["predecessor_said"][0]["ref"]})
    blob = json.dumps(art, ensure_ascii=False)
    assert long_think not in blob, "control: the WHOLE thinking text is not present"
    assert handoff.leak_fragments(art, lines), "a 300-char truncated leak was not seen"
    assert any("FRAGMENT" in b for b in handoff.verify(art, lines)),         handoff.verify(art, lines)


check("truncated leakage: a 300-char slice of a thinking block is caught although "
      "whole-string containment cannot see it", t1_truncated_leak)


def t1_leak_false_positive():
    """The scan must not make a record unpublishable for saying what the USER
    said. A model that quotes its instructions verbatim inside its reasoning
    shares a long run with an admissible quotation — that is not a leak."""
    shared = ("USER-INSTRUCTION-4 build the widget exactly as specified, "
              "test it against the fixture, and do not deploy it under any "
              "circumstances whatsoever until the coordinator has reviewed it")
    assert len(shared) > handoff.LEAK_WINDOW * 2
    lines = list(LINES)
    lines.append(json.dumps(
        {"type": "user", "timestamp": "2026-09-05T00:00:30Z", "uuid": "uF",
         "message": {"role": "user", "content": shared}}) + "\n")
    lines.append(json.dumps(
        {"type": "assistant", "timestamp": "2026-09-05T00:00:31Z", "uuid": "aF",
         "message": {"id": "mF", "role": "assistant", "model": "x", "content": [
             {"type": "thinking",
              "thinking": "The user said: " + shared + " — so I will not deploy."}]}}
    ) + "\n")
    art = handoff.capture(nid="a1", node=NODE, lines=lines, views_all=VIEWS,
                          mailbox=MAIL, mooted_ask=ASK, grants=[GRANT],
                          boundary=BOUNDARY, at="2026-09-05T00:01:00Z")
    assert any(shared == i["text"] for i in art["record"]["instructions_received"]),         "control: the shared text really is quoted in the record"
    assert handoff.leak_fragments(art, lines) == [],         "the shared user text was reported as a reasoning leak"
    assert handoff.verify(art, lines) == []


check("scan false-positive control: reasoning that quotes the user verbatim does "
      "not make the record unpublishable", t1_leak_false_positive)


def t1_exact_overlap():
    """THE EXACT CHECK USES THE SAME ADMISSIBILITY RULE AS THE SCAN (decision,
    Astra review 2026-09-05 11:52Z). A whole private string that ALSO exists
    verbatim in an admissible source is not refused: those bytes are the
    user's or the agent's own visible words, and the record is quoting the
    source, not the reasoning. The two used to disagree — the scan exempted
    such an overlap and this check refused it — and the refusal was not
    theoretical: a short thought like a one-line plan is often written out
    again in the visible reply, and every such boundary would have published
    NO record at all.

    The control below is the same length of private text with no admissible
    twin: still refused."""
    twin = "Checking the fixture before the widget is written, then reporting."
    assert len(twin) < handoff.LEAK_GUARANTEE, "the window scan must not cover it"
    lines = list(LINES)
    lines.append(json.dumps(
        {"type": "assistant", "timestamp": "2026-09-05T00:00:40Z", "uuid": "aX",
         "message": {"id": "mX", "role": "assistant", "model": "x", "content": [
             {"type": "thinking", "thinking": twin},
             {"type": "text", "text": twin}]}}) + "\n")
    art = handoff.capture(nid="a1", node=NODE, lines=lines, views_all=VIEWS,
                          mailbox=MAIL, mooted_ask=ASK, grants=[GRANT],
                          boundary=BOUNDARY, at="2026-09-05T00:01:00Z")
    assert any(i["text"] == twin for i in art["record"]["predecessor_said"]),         "control: the visible twin really is quoted in the record"
    assert twin in json.dumps(art, ensure_ascii=False),         "control: the private string's bytes really are in the artifact"
    assert handoff.verify(art, lines) == [], handoff.verify(art, lines)

    # …and the same thing with no admissible twin is still refused
    only_private = "A thought that was never said out loud to anyone at all."
    lines2 = list(LINES)
    lines2.append(json.dumps(
        {"type": "assistant", "timestamp": "2026-09-05T00:00:41Z", "uuid": "aY",
         "message": {"id": "mY", "role": "assistant", "model": "x", "content": [
             {"type": "thinking", "thinking": only_private},
             {"type": "text", "text": "done"}]}}) + "\n")
    art2 = handoff.capture(nid="a1", node=NODE, lines=lines2, views_all=VIEWS,
                           mailbox=MAIL, mooted_ask=ASK, grants=[GRANT],
                           boundary=BOUNDARY, at="2026-09-05T00:01:00Z")
    assert handoff.verify(art2, lines2) == []
    art2["record"]["predecessor_said"].append(
        {"role": "assistant", "text": only_private, "truncated": False,
         "ref": art2["record"]["predecessor_said"][0]["ref"]})
    bad = handoff.verify(art2, lines2)
    assert any("private reasoning/signature present" in b for b in bad), bad


check("exact whole-string check: an admissible verbatim twin is DELIBERATELY not "
      "refused; the same text with no twin still is", t1_exact_overlap)


def t1_scan_shape():
    """The scan's memory is FLAT (Astra review: it must not balloon). The
    filter is a fixed-size bitmap whatever the input, and the guarantee it
    states is the one it keeps."""
    small, big = handoff._bitmap(["x" * 100]), handoff._bitmap([TRICKY * 2000])
    assert len(small) == len(big) == handoff._FILTER_BITS >> 3, len(big)
    assert handoff.LEAK_GUARANTEE == handoff.LEAK_WINDOW + handoff.LEAK_STEP - 1
    # a shared run AT the guarantee length is found wherever it starts
    priv = "Z" + "".join(chr(0x61 + (i * 7) % 26) for i in range(400))
    for start in (0, 1, 17, 33, 64, 130):
        run = priv[start:start + handoff.LEAK_GUARANTEE]
        art = {"kind": handoff.KIND, "v": handoff.V,
               "record": {"predecessor_said": [{"text": "…" + run + "…"}]},
               "inputs": {}}
        lines = [json.dumps({"type": "assistant", "uuid": "a", "message": {
            "id": "m", "role": "assistant", "model": "x", "content": [
                {"type": "thinking", "thinking": priv}]}}) + "\n"]
        assert handoff.leak_fragments(art, lines),             f"a {handoff.LEAK_GUARANTEE}-char shared run at offset {start} was missed"


check("scan memory is flat (fixed filter) and its stated guarantee holds at every "
      "offset", t1_scan_shape)

# ── §2 artifact forgeries ──────────────────────────────────────────────────
print("\n§2 artifact forgeries after a clean build")


def _projected(a):
    it = next(i for i in a["record"]["instructions_received"] if i.get("projected"))
    it["text"] = "FROM boss · request\nUSER-INSTRUCTION-2 ship by NOON"


expect_reject("changed projected quotation text", _projected)
expect_reject("changed unprojected quotation text", lambda a: a["record"][
    "instructions_received"][0].__setitem__("text", "USER-INSTRUCTION-1 deploy tonight"))
expect_reject("assistant text relabelled as user", lambda a: a["record"][
    "predecessor_said"][-1].__setitem__("role", "user"))
expect_reject("forged pair name", lambda a: a["record"]["tool_pairs"][0]
              .__setitem__("name", "Bash"))
expect_reject("forged pair id", lambda a: a["record"]["tool_pairs"][0]
              .__setitem__("id", "t9"))
expect_reject("forged result excerpt", lambda a: a["record"]["tool_pairs"][0]["result"]
              .__setitem__("excerpt", "wrote nothing"))
expect_reject("forged result length", lambda a: a["record"]["tool_pairs"][0]["result"]
              .__setitem__("chars", 1))


def _pair_paired(a):
    p = next(p for p in a["record"]["tool_pairs"] if p["id"] == "t4")
    p["paired"] = True
    p["result"] = copy.deepcopy(a["record"]["tool_pairs"][0]["result"])


expect_reject("unpaired call marked paired with a borrowed result", _pair_paired)
expect_reject("invented tool pair", lambda a: a["record"]["tool_pairs"].append(
    {"id": "t-new", "name": "Bash", "command": "rm -rf /", "paired": False,
     "ref": a["record"]["tool_pairs"][0]["ref"]}))
expect_reject("thinking omission count changed", lambda a: next(
    o for o in a["record"]["omissions"] if o["kind"] == "thinking_block").__setitem__("count", 0))
expect_reject("orphan omission removed", lambda a: a["record"].__setitem__(
    "omissions", [o for o in a["record"]["omissions"] if o["kind"] != "orphan_tool_result"]))
expect_reject("omission kind duplicated with a wrong count", lambda a: a["record"][
    "omissions"].append({"kind": "sidechain_row", "count": 5}))
expect_reject("forged predecessor status claim", lambda a: a["record"][
    "predecessor_claims"][0]["input"].__setitem__("summary", "STATUS-CLAIM widget DONE"))
expect_reject("record.seat differs from captured seat", lambda a: a["record"][
    "seat"].__setitem__("charter", "deploy everything"))
expect_reject("continuity claim", lambda a: a["record"]["continuity"]
              .__setitem__("provider_context", "carried"))
expect_reject("outside-grant artifact added", lambda a: a["record"]["artifacts"].append(
    {"path": OUTSIDE, "tool": "Write", "writes": 1,
     "first_ref": a["record"]["tool_pairs"][0]["ref"], "disk": {"exists": False}}))
expect_reject("captured grants widened (rebuild disagrees)", lambda a: a["inputs"][
    "grants"].append(tempfile.gettempdir()))


def _view_consistent(a):
    h = handoff.sha256(ENVELOPE)
    new = "FROM boss · request\nUSER-INSTRUCTION-2 ship by NOON"
    a["inputs"]["views"][h] = new
    it = next(i for i in a["record"]["instructions_received"] if i.get("projected"))
    it["text"], it["projected"]["visible_sha256"] = new, handoff.sha256(new)


def t_view_unanchored():
    a = build()
    _view_consistent(a)
    assert handoff.verify(a, LINES) == [], \
        "expected: a CONSISTENT view forgery passes without the sidecar anchor"


check("DECLARED LIMIT: consistent forged view row passes WITHOUT the sidecar anchor",
      t_view_unanchored)
expect_reject("consistent forged view row (with sidecar anchor)", _view_consistent,
              anchors={"views_all": VIEWS})


def _seat_consistent(a):
    a["inputs"]["seat"]["charter"] = "deploy everything"
    a["record"]["seat"]["charter"] = "deploy everything"


def t_seat_unanchored():
    a = build()
    _seat_consistent(a)
    assert handoff.verify(a, LINES) == []


check("DECLARED LIMIT: consistent forged seat snapshot passes WITHOUT the node anchor",
      t_seat_unanchored)
expect_reject("consistent forged seat snapshot (with node anchor)", _seat_consistent,
              anchors={"seat": NODE})


def _mail_consistent(a):
    a["inputs"]["mailbox"][0]["body"] = "PENDING-MAIL deploy now"
    a["record"]["mail"]["pending"][0]["body"] = "PENDING-MAIL deploy now"


expect_reject("consistent forged mail snapshot (with mailbox anchor)", _mail_consistent,
              anchors={"mailbox": MAIL})


def t_tampered():
    a = build()
    t = list(LINES)
    t[0] = t[0].replace("never deploy", "deploy tonight")
    bad = handoff.verify(a, t)
    assert any("transcript does not match" in b for b in bad), bad
    assert any("hash mismatch" in b for b in bad), bad


check("tampered transcript line: transcript hash AND line hash both reported", t_tampered)


def t_memo_warmed_then_altered():
    """A WARMED memo must not answer for a different transcript (Astra review
    2026-09-05 12:10Z, found in the shipped code). The core result is
    remembered so a publication does not rebuild and re-scan twice, and the
    first cut keyed it on `inputs.transcript.sha256` — the hash the ARTIFACT
    CLAIMS — so once a genuine verify had warmed the entry, the same artifact
    handed an altered file of the same line count got the cached [] back and
    V0 never ran again.

    Order matters here: warm with the genuine bytes FIRST, or this check
    passes for the wrong reason."""
    art = build()
    assert handoff.verify(art, LINES) == [], "control: the genuine pair verifies"
    altered = list(LINES)
    altered[0] = altered[0].replace("never deploy", "deploy tonight")
    assert len(altered) == len(LINES), "the alteration must keep the line count"
    assert "".join(altered) != "".join(LINES)
    bad = handoff.verify(art, altered)
    assert any("transcript does not match" in b for b in bad),         f"a warmed memo accepted an altered transcript: {bad}"
    assert handoff.verify(art, LINES) == [], "the genuine pair stopped verifying"
    # the same shape through the anchored call the boundary actually makes
    assert handoff.verify(art, LINES, views_all=VIEWS, seat=NODE, mailbox=MAIL) == []
    assert handoff.verify(art, altered, views_all=VIEWS, seat=NODE, mailbox=MAIL)


check("a warmed verify memo still rejects an altered same-length transcript, and the "
      "genuine one still passes", t_memo_warmed_then_altered)

# ── §3 zero-omission control ───────────────────────────────────────────────
print("\n§3 zero-omission input (the anti-vacuity control for the omission checks)")
CLEAN = [json.dumps(r) + "\n" for r in [
    {"type": "user", "timestamp": "2026-09-05T00:00:01Z", "uuid": "c1",
     "message": {"role": "user", "content": "just say hi"}},
    {"type": "assistant", "timestamp": "2026-09-05T00:00:02Z", "uuid": "c2",
     "message": {"id": "cm", "role": "assistant", "model": "x",
                 "content": [{"type": "text", "text": "hi"}]}}]]


def t3():
    a = handoff.capture(nid="a1", node=NODE, lines=CLEAN, views_all={}, mailbox=[],
                        mooted_ask=None, grants=[GRANT], boundary=BOUNDARY)
    assert a["record"]["omissions"] == [], a["record"]["omissions"]
    assert handoff.verify(a, CLEAN, views_all={}, seat=NODE, mailbox=[]) == []
    a["record"]["omissions"].append({"kind": "thinking_block", "count": 1})
    assert handoff.verify(a, CLEAN), "forged omission on a clean input not caught"


check("zero omissions verifies clean; a forged omission on it is still caught", t3)

# ── §4 escaping ────────────────────────────────────────────────────────────
print("\n§4 escaping")


def t4():
    it = next(i for i in ART["record"]["instructions_received"] if i["text"] == TRICKY)
    assert it["ref"]["line"] == 10
    assert handoff.verify(json.loads(json.dumps(ART, ensure_ascii=False)), LINES) == []
    assert handoff.verify(json.loads(json.dumps(ART, ensure_ascii=True)), LINES) == []
    a = build()
    next(i for i in a["record"]["instructions_received"] if i["text"] == TRICKY)["text"] = \
        TRICKY.replace("\n", "\\n")
    assert handoff.verify(a, LINES), "re-escaped newline not caught"
    a = build()
    next(i for i in a["record"]["instructions_received"] if i["text"] == TRICKY)["text"] = \
        TRICKY.replace("\\path", "/path")
    assert handoff.verify(a, LINES), "changed backslash not caught"


check("quotes/backslashes/tabs/newlines/unicode round-trip; re-escaping is caught", t4)

FLAG_PATH = os.path.join(DATA, "handoff.flag")

# ── §5 publication ─────────────────────────────────────────────────────────
print("\n§5 atomic publication (staged directory + one rename)")


def _staging(d):
    return [x for x in os.listdir(d) if x.startswith(".handoff-")]


def t5_fail_paths():
    d = tempfile.mkdtemp(prefix="handoff-gen-")
    bad = build()
    bad["record"]["continuity"]["provider_context"] = "carried"
    assert handoff.write_generation(d, 0, bad, LINES) is None, "unverifiable record published"
    assert os.listdir(d) == [], os.listdir(d)
    orig = handoff.render_md
    handoff.render_md = lambda a: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        assert handoff.write_generation(d, 0, ART, LINES) is None
    finally:
        handoff.render_md = orig
    assert os.listdir(d) == [], f"partial publication left behind: {os.listdir(d)}"
    orig_rename = handoff.os.rename
    handoff.os.rename = lambda a, b: (_ for _ in ()).throw(OSError("crash mid-publish"))
    try:
        assert handoff.write_generation(d, 0, ART, LINES) is None
    finally:
        handoff.os.rename = orig_rename
    assert os.listdir(d) == [], f"failed rename left behind: {os.listdir(d)}"
    OBS["publish_failures_clean"] = True


check("failing verify / failing render / failing rename each leave NOTHING (no staging dir)",
      t5_fail_paths)


def t5_success():
    d = tempfile.mkdtemp(prefix="handoff-gen-")
    out = handoff.write_generation(d, 0, ART, LINES)
    assert out and os.path.isdir(out), out
    assert sorted(os.listdir(out)) == ["manifest.json", "record.json", "record.md"]
    assert _staging(d) == []
    got = handoff.read_generation(d, 0)
    assert got and got["generation"] == 0
    assert handoff.verify(got["record"], LINES) == []
    before = open(os.path.join(out, handoff.RECORD_JSON), "rb").read()
    assert handoff.write_generation(d, 0, build(), LINES) is None, "generation overwritten"
    assert open(os.path.join(out, handoff.RECORD_JSON), "rb").read() == before
    assert handoff.write_generation(d, 1, ART, LINES)
    assert sorted(os.listdir(d)) == ["handoff-g0", "handoff-g1"]


check("success publishes record.json+record.md+manifest.json; same generation refused; "
      "next generation is separate", t5_success)


def t5_reader():
    d = tempfile.mkdtemp(prefix="handoff-read-")
    out = handoff.write_generation(d, 0, ART, LINES)
    assert handoff.read_generation(d, 0)
    assert handoff.read_generation(d, 1) is None, "a generation that was never written"
    os.remove(os.path.join(out, handoff.MANIFEST))
    assert handoff.read_generation(d, 0) is None, "manifest-less directory read as a record"
    d2 = tempfile.mkdtemp(prefix="handoff-read-")
    out2 = handoff.write_generation(d2, 0, ART, LINES)
    p = os.path.join(out2, handoff.RECORD_MD)
    body = open(p, "rb").read()
    with open(p, "wb") as f:                       # same length, different bytes
        f.write(b"X" + body[1:])
    assert handoff.read_generation(d2, 0) is None, "corrupted record.md read as a record"
    d3 = tempfile.mkdtemp(prefix="handoff-read-")
    out3 = handoff.write_generation(d3, 0, ART, LINES)
    p3 = os.path.join(out3, handoff.RECORD_JSON)
    body3 = open(p3, "rb").read()
    with open(p3, "wb") as f:
        f.write(body3[:-5])                        # truncated publication
    assert handoff.read_generation(d3, 0) is None, "truncated record.json read as a record"


check("reader refuses a manifest-less, corrupted or truncated generation directory", t5_reader)


def t5_malformed_manifest():
    """A manifest that is VALID JSON but the wrong shape must answer "no
    record", never raise — the caller is on the path that builds an agent's
    identity prompt (Astra review 2026-09-05: `files[name]` was assumed to be
    a dict and `.get` on a string raises AttributeError)."""
    d = tempfile.mkdtemp(prefix="handoff-bad-")
    out = handoff.write_generation(d, 0, ART, LINES)
    good = json.load(open(os.path.join(out, handoff.MANIFEST), encoding="utf-8"))
    assert handoff.read_generation(d, 0), "control: the intact record reads"

    def manifest(obj):
        with open(os.path.join(out, handoff.MANIFEST), "w", encoding="utf-8") as f:
            json.dump(obj, f)

    bad_shapes = [
        {**good, "files": {handoff.RECORD_JSON: "not-a-dict",
                           handoff.RECORD_MD: good["files"][handoff.RECORD_MD]}},
        {**good, "files": {handoff.RECORD_JSON: {"sha256": 5, "bytes": "x"},
                           handoff.RECORD_MD: good["files"][handoff.RECORD_MD]}},
        {**good, "files": {}},
        {**good, "files": []},
        {**good, "v": 99},
        {**good, "kind": "something.else"},
        {**good, "generation": 7},
        {**good, "node": "someone-else"},
        [good],
        "not-an-object",
        None,
    ]
    for obj in bad_shapes:
        manifest(obj)
        got = handoff.read_generation(d, 0, node=str(good.get("node")))
        assert got is None, f"malformed manifest accepted: {str(obj)[:70]}"
    manifest(good)
    assert handoff.read_generation(d, 0, node=str(good.get("node"))),         "the intact record stopped reading after the malformed ones"
    assert handoff.read_generation(d, 0, node="a-different-seat") is None,         "a record published for another seat was accepted as this seat's"


check("reader answers None (never raises) for 11 malformed-but-valid-JSON manifests, "
      "and binds the node", t5_malformed_manifest)



# ── §6 the real door ───────────────────────────────────────────────────────
print("\n§6 real cross-provider switch_model + export_predecessor_transcript")

def new_sid() -> str:
    """A FRESH session id per seeded session: `transcript_path` globs
    `projects/*/<sid>.jsonl` across every org, so a session id reused by two
    fixtures would let one org's export copy another org's transcript."""
    return str(uuid.uuid4())


def make_org(name: str, tier: str = "luna"):
    org = store.create_org(f"zz handoff {name}")
    nid = org.hire(USER, None, tier, 4, "a1", add_dirs=[{"path": GRANT}],
                   tools={"bash": True, "web": False, "edit": True,
                          "subagents": False, "mcp": []},
                   org_visibility="team", charter="build widgets")["node"]
    store.save_org(org)
    return org.d["slug"], nid


def seed_session(slug: str, nid: str, recs=None) -> str:
    sid = new_sid()
    with store.DOC_LOCK:
        o = store.load_org(slug)
        n = o.node(nid)
        n["session_id"] = sid
        n.pop("session_unrun", None)
        n["codex_thread"] = sid
        n["last_status"] = NODE["last_status"]
        o.ask_user(nid, "MOOTED-ASK which colour?", options=["red"])
        o.post_mail(USER, nid, "PENDING-MAIL please also test it", kind="request")
        store.save_org(o)
    supervisor._codex_journal(slug, sid, list(recs if recs is not None else RECS))
    supervisor._record_prompt_view(slug, sid, ENVELOPE, VISIBLE)
    return sid


def cross(slug: str, nid: str, tier: str = "opus"):
    """A real crossing + the real export door; returns (org, result, dst)."""
    with store.DOC_LOCK:
        o = store.load_org(slug)
        r = o.switch_model(USER, nid, tier)
        dst = supervisor.export_predecessor_transcript(
            o, nid, old_sid=str(r.get("old_session") or ""),
            reason="switch_model")     # what the real switch doors pass
        store.save_org(o)
    return store.load_org(slug), r, dst


SLUG6, NID6 = make_org("door")
SID6 = seed_session(SLUG6, NID6)
T0 = time.perf_counter()
ORG6, R6, DST6 = cross(SLUG6, NID6)
CROSS_SECONDS = time.perf_counter() - T0
SD6 = supervisor.scratch_dir(SLUG6, NID6)
GEN6 = int(ORG6.node(NID6)["generation"]) - 1


def t6():
    assert R6.get("old_session") == SID6 and R6.get("bearer"), R6
    assert DST6 and os.path.isfile(DST6), DST6
    got = handoff.read_generation(SD6, GEN6)
    assert got, f"no published record in {SD6}: {sorted(os.listdir(SD6))}"
    lines = open(DST6, encoding="utf-8").read().splitlines(keepends=True)
    n = ORG6.node(NID6)
    loaded = supervisor._load_prompt_views(SLUG6, SID6)
    views_all = {h: rows[-1]["visible"] for h, rows in loaded.items() if rows}
    assert views_all, "control: the sidecar row was not written or not read"
    box = list((ORG6.d.get("mail") or {}).get(NID6) or [])
    bad = handoff.verify(got["record"], lines, views_all=views_all, seat=n, mailbox=box)
    assert bad == [], bad
    rec = got["record"]["record"]
    assert any(i["text"] == VISIBLE for i in rec["instructions_received"]), \
        "the projected (human) text of the machine envelope is missing"
    assert rec["mail"]["mooted_ask"]["status"] == "moot"
    assert rec["mail"]["pending"] and "PENDING-MAIL" in rec["mail"]["pending"][0]["body"]
    assert rec["seat"]["charter"] == "build widgets"
    b = got["record"]["inputs"]["boundary"]
    assert b["from"]["provider"] == "openai" and b["to"]["provider"] == "claude", b
    for s in EXCLUDED:
        assert s not in got[handoff.RECORD_MD], f"leaked through the real door: {s!r}"
    assert _staging(SD6) == [], _staging(SD6)
    OBS["real_door"] = {"bearer": R6["bearer"], "generation": GEN6,
                        "scratch": sorted(os.listdir(SD6)),
                        "record_md_bytes": len(got[handoff.RECORD_MD].encode("utf-8")),
                        "seconds_switch_export": round(CROSS_SECONDS, 3)}


check("the real door publishes handoff-g<gen>/ and it verifies against live anchors", t6)


def t6_second_boundary():
    """A second crossing on the same seat writes its OWN generation and does
    not touch the first — the transcript copy is overwritten, the records are
    not."""
    seed_session(SLUG6, NID6, RECS[:6])
    first = open(os.path.join(handoff.generation_dir(SD6, GEN6),
                              handoff.RECORD_JSON), "rb").read()
    org2, r2, dst2 = cross(SLUG6, NID6, "luna")
    gen2 = int(org2.node(NID6)["generation"]) - 1
    assert gen2 == GEN6 + 1, (gen2, GEN6)
    assert handoff.read_generation(SD6, gen2), sorted(os.listdir(SD6))
    assert open(os.path.join(handoff.generation_dir(SD6, GEN6),
                             handoff.RECORD_JSON), "rb").read() == first, \
        "the earlier generation was rewritten"
    OBS["two_generations"] = sorted(x for x in os.listdir(SD6) if x.startswith("handoff-g"))


check("a second boundary publishes its own generation and leaves the earlier one byte-intact",
      t6_second_boundary)


def t6_boundary_reason():
    """The record names WHICH boundary it came from, and that name is the
    door's own (Astra review 2026-09-05: inferring it from tier equality
    labels a same-tier reseed "cheap_compact" — invented provenance). A caller
    that supplies nothing gets a truthful generic label instead."""
    got = handoff.read_generation(SD6, GEN6, node=NID6)
    assert got, "control: the §6 record is readable"
    assert got["record"]["inputs"]["boundary"]["reason"] == "switch_model",         got["record"]["inputs"]["boundary"]

    slug, nid = make_org("reason-cheap")
    seed_session(slug, nid)
    with store.DOC_LOCK:
        o = store.load_org(slug)
        r = o.cheap_compact(USER, nid)
        supervisor.export_predecessor_transcript(
            o, nid, old_sid=str(r.get("old_session") or ""), reason="cheap_compact")
        store.save_org(o)
    o = store.load_org(slug)
    sd = supervisor.scratch_dir(slug, nid)
    g = int(o.node(nid)["generation"]) - 1
    rec = handoff.read_generation(sd, g, node=nid)
    assert rec, sorted(os.listdir(sd))
    b = rec["record"]["inputs"]["boundary"]
    assert b["reason"] == "cheap_compact", b
    # a same-tier boundary: the OLD inference would have called this one
    # "cheap_compact" whatever it really was, which is the defect
    assert b["from"]["tier"] == b["to"]["tier"], b

    slug2, nid2 = make_org("reason-unstated")
    seed_session(slug2, nid2)
    with store.DOC_LOCK:
        o2 = store.load_org(slug2)
        r2 = o2.cheap_compact(USER, nid2)
        supervisor.export_predecessor_transcript(
            o2, nid2, old_sid=str(r2.get("old_session") or ""))
        store.save_org(o2)
    o2 = store.load_org(slug2)
    g2 = int(o2.node(nid2)["generation"]) - 1
    rec2 = handoff.read_generation(supervisor.scratch_dir(slug2, nid2), g2, node=nid2)
    assert rec2 and rec2["record"]["inputs"]["boundary"]["reason"] == "session_replaced",         rec2["record"]["inputs"]["boundary"] if rec2 else None
    OBS["boundary_reasons"] = ["switch_model", "cheap_compact", "session_replaced"]


check("the boundary reason is the door's own; an unstated one is a truthful generic, "
      "not a guess from tier equality", t6_boundary_reason)


def t6_doors_state_their_reason():
    """Every call site passes one — a door added without a reason would
    publish `session_replaced`, which is honest, but the seven that exist are
    named."""
    named = 0
    for rel in ("orgtree/api.py", "orgtree/supervisor.py"):
        src = open(os.path.join(REPO, "backend", rel), encoding="utf-8").read()
        for chunk in src.split("export_predecessor_transcript(")[1:]:
            head = chunk[:260]
            if head.lstrip().startswith("org") or head.lstrip().startswith("o2"):
                named += 1 if "reason=" in head else 0
    assert named == 7, f"{named} of 7 call sites name their boundary reason"


check("all 7 doors name their own boundary reason", t6_doors_state_their_reason)

# ── §7 the seven doors ─────────────────────────────────────────────────────
print("\n§7 every boundary door goes through the one publishing function")


def t7():
    doors = []
    for rel in ("orgtree/api.py", "orgtree/supervisor.py"):
        src = open(os.path.join(REPO, "backend", rel), encoding="utf-8").read()
        for i, line in enumerate(src.splitlines(), 1):
            if "export_predecessor_transcript(" in line and not line.strip().startswith("#"):
                doors.append((rel, i, line.strip()[:60]))
    calls = [d for d in doors if not d[2].startswith("def ")]
    assert len(calls) == 7, f"expected 7 call sites, found {len(calls)}: {calls}"
    src = open(os.path.join(REPO, "backend", "orgtree", "supervisor.py"),
               encoding="utf-8").read()
    body = src.split("def export_predecessor_transcript(", 1)[1].split("\ndef ", 1)[0]
    assert "_publish_handoff_record(" in body, \
        "publication is not inside the function every door calls"
    OBS["doors"] = calls


check("all 7 export call sites exist and publication lives inside that one function", t7)

# ── §8 flag-off compatibility, measured against main ───────────────────────
print("\n§8 flag OFF: prompt byte-identical to main's own supervisor, no notice")

FLAG = FLAG_PATH
CHILD = r"""
import os, sys
sys.path.insert(0, sys.argv[1])
os.environ["ORGTREE_DATA"] = sys.argv[2]
os.environ["ORGTREE_PORT"] = "9"
from orgtree import store, supervisor
assert os.path.realpath(store.DATA_ROOT) == os.path.realpath(sys.argv[2])
org = store.load_org(sys.argv[3])
sys.stdout.buffer.write(supervisor.identity_prompt(org, sys.argv[4]).encode("utf-8"))
"""


def _pkg_copy(from_main: bool) -> str:
    """A private copy of the backend package; with `from_main`, supervisor.py
    and ledger.py come from `main` and handoff.py does not exist at all."""
    tmp = tempfile.mkdtemp(prefix="handoff-main-" if from_main else "handoff-branch-")
    shutil.copytree(os.path.join(REPO, "backend", "orgtree"),
                    os.path.join(tmp, "orgtree"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    if from_main:
        for f in ("supervisor.py", "ledger.py"):
            r = subprocess.run(["git", "-C", REPO, "show", f"main:backend/orgtree/{f}"],
                               capture_output=True)
            assert r.returncode == 0 and r.stdout, r.stderr[:300]
            with open(os.path.join(tmp, "orgtree", f), "wb") as fh:
                fh.write(r.stdout)
        os.remove(os.path.join(tmp, "orgtree", "handoff.py"))
    return tmp


def prompt_from(pkg: str, slug: str, nid: str) -> bytes:
    r = subprocess.run([sys.executable, "-c", CHILD, pkg, DATA, slug, nid],
                       capture_output=True, timeout=300)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")[-1500:]
    return r.stdout


MAIN_PKG = _pkg_copy(True)
BRANCH_PKG = _pkg_copy(False)


SLUG8, NID8 = make_org("prompt")
seed_session(SLUG8, NID8)
ORG8, R8, DST8 = cross(SLUG8, NID8)
SD8 = supervisor.scratch_dir(SLUG8, NID8)
GEN8 = int(ORG8.node(NID8)["generation"]) - 1


def t8_marker():
    """The splice rides the SAME first-turn marker as the breadcrumbs block.
    `_archive_session_in_place` (ledger.py) sets it for EVERY session-
    replacing boundary — cheap_compact, reseed and a cross-provider
    switch_model alike — so the record reaches a crossing successor the same
    way its breadcrumbs do. Asserted here, not assumed: this test's fixtures
    would otherwise be setting the marker by hand and proving nothing."""
    assert ORG8.node(NID8).get("cheap_compacted") is True, \
        "a real crossing did not set the first-turn marker the splice rides"


check("a real crossing sets the first-turn marker itself (the splice gate is not "
      "set up by hand)", t8_marker)


def t8_off():
    assert not os.path.exists(FLAG), "the flag must start off"
    assert not supervisor.handoff_flag_on()
    org = store.load_org(SLUG8)
    assert org.node(NID8).get("cheap_compacted"), \
        "control: the seat carries the first-turn marker, so a block COULD render"
    assert handoff.read_generation(SD8, GEN8), "control: a record IS on disk to be spliced"
    assert supervisor._handoff_block(org, NID8) == "", "flag off but a block was rendered"
    mine = supervisor.identity_prompt(org, NID8).encode("utf-8")
    branch = prompt_from(BRANCH_PKG, SLUG8, NID8)
    assert mine == branch, "control: the same code disagrees with itself across processes"
    main = prompt_from(MAIN_PKG, SLUG8, NID8)
    assert mine == main, (
        f"flag-off prompt differs from main's: {len(mine)} vs {len(main)} bytes")
    OBS["flag_off_prompt_bytes"] = len(mine)


check("flag OFF: identity prompt byte-identical to main's supervisor (control: "
      "same-code comparison agrees)", t8_off)


def _notices_across_a_boundary(name: str) -> list[str]:
    slug, nid = make_org(name)
    seed_session(slug, nid)
    org, r, dst = cross(slug, nid)
    assert handoff.read_generation(supervisor.scratch_dir(slug, nid),
                                   int(org.node(nid)["generation"]) - 1), \
        "control: the record was published, so a notice COULD have been added"
    return [x["text"] for x in (org.d.get("notices") or {}).get(nid) or []]


def t8_notice():
    off = _notices_across_a_boundary("notice-off")
    open(FLAG, "w").close()
    try:
        on = _notices_across_a_boundary("notice-on")
    finally:
        os.remove(FLAG)
    assert not any("handoff" in t.lower() for t in off), [t[:80] for t in off]
    extra = [t for t in on if "handoff-g" in t]
    assert len(on) == len(off) + 1 and len(extra) == 1, \
        f"flag off {len(off)} notices, flag on {len(on)}: {[t[:60] for t in on]}"
    assert [t for t in on if t not in extra] == off, "the ledger's own notices changed"
    OBS["notices"] = {"flag_off": [t[:60] for t in off], "handoff_notice": extra[0]}


check("flag OFF: the boundary adds no handoff notice; flag ON adds exactly one "
      "(differential control)", t8_notice)


def t8_on():
    open(FLAG, "w").close()
    try:
        assert supervisor.handoff_flag_on()
        slug, nid = SLUG8, NID8
        o = store.load_org(slug)
        # the splice is gated on the same first-turn marker as breadcrumbs
        p = supervisor.identity_prompt(o, nid)
        assert p.count("[HANDOFF RECORD —") == 1, p.count("[HANDOFF RECORD —")
        assert "USER-INSTRUCTION-1" in p, "the spliced block carries no quoted instruction"
        for s in EXCLUDED:
            assert s not in p, f"leaked into the prompt: {s!r}"
        main = prompt_from(MAIN_PKG, slug, nid)
        assert p.encode("utf-8") != main, \
            "positive control: flag ON must differ from main, or the comparison is inert"
        OBS["flag_on_extra_bytes"] = len(p.encode("utf-8")) - len(main)
        # FIRST TURN ONLY: the same clearing that retires the breadcrumb splice
        with store.DOC_LOCK:
            o2 = store.load_org(slug)
            o2.node(nid).pop("cheap_compacted", None)
            store.save_org(o2)
        o2 = store.load_org(slug)
        assert supervisor._handoff_block(o2, nid) == "", \
            "the block rendered on a turn after the first"
        assert supervisor.identity_prompt(o2, nid).encode("utf-8") == \
            prompt_from(MAIN_PKG, slug, nid), \
            "after the marker clears, the prompt must be main's again"
        with store.DOC_LOCK:                       # restore for later sections
            o3 = store.load_org(slug)
            o3.node(nid)["cheap_compacted"] = True
            store.save_org(o3)
    finally:
        os.remove(FLAG)
    assert not supervisor.handoff_flag_on()


check("flag ON: notice + exactly one first-turn block, and it DIFFERS from main "
      "(the comparison is not inert)", t8_on)

# ── §9 generation-specific lookup ──────────────────────────────────────────
print("\n§9 the successor reads its own predecessor's generation only")


def t9():
    slug, nid = make_org("generation")
    seed_session(slug, nid)
    org, r, _ = cross(slug, nid)
    sd = supervisor.scratch_dir(slug, nid)
    gen = int(org.node(nid)["generation"]) - 1
    open(FLAG, "w").close()
    try:
        with store.DOC_LOCK:
            o = store.load_org(slug)
            o.node(nid)["cheap_compacted"] = True
            store.save_org(o)
        o = store.load_org(slug)
        assert "[HANDOFF RECORD —" in supervisor._handoff_block(o, nid)
        # a record for an OLDER generation is not this boundary's record
        os.rename(handoff.generation_dir(sd, gen), handoff.generation_dir(sd, gen - 1))
        assert supervisor._handoff_block(o, nid) == "", \
            "a stale generation was spliced as this boundary's record"
        os.rename(handoff.generation_dir(sd, gen - 1), handoff.generation_dir(sd, gen))
        assert "[HANDOFF RECORD —" in supervisor._handoff_block(o, nid)
        # a corrupted current generation is not spliced either, and does not raise
        p = os.path.join(handoff.generation_dir(sd, gen), handoff.RECORD_MD)
        body = open(p, "rb").read()
        with open(p, "wb") as f:
            f.write(b"Z" + body[1:])
        assert supervisor._handoff_block(o, nid) == "", \
            "a corrupted record was spliced"
        with open(p, "wb") as f:
            f.write(body)
        assert "[HANDOFF RECORD —" in supervisor._handoff_block(o, nid)
    finally:
        os.remove(FLAG)


check("stale generation and corrupted generation are both refused; the right one splices", t9)


def t5_identity_survives_a_broken_record():
    """The same thing through the real door: a broken record must cost the
    successor a BLOCK, not a prompt."""
    slug, nid = make_org("broken-record")
    seed_session(slug, nid)
    org, r, _ = cross(slug, nid)
    sd = supervisor.scratch_dir(slug, nid)
    gen = int(org.node(nid)["generation"]) - 1
    open(FLAG_PATH, "w").close()
    try:
        assert "[HANDOFF RECORD —" in supervisor._handoff_block(org, nid),             "control: the intact record does splice"
        mpath = os.path.join(handoff.generation_dir(sd, gen), handoff.MANIFEST)
        with open(mpath, "w", encoding="utf-8") as f:
            json.dump({"v": handoff.V, "kind": handoff.KIND, "node": nid,
                       "generation": gen, "files": {handoff.RECORD_JSON: "oops",
                                                    handoff.RECORD_MD: 3}}, f)
        assert supervisor._handoff_block(org, nid) == "", "a broken record spliced"
        p = supervisor.identity_prompt(org, nid)
        assert p and "HANDOFF RECORD" not in p, "identity prompt did not survive"
    finally:
        os.remove(FLAG_PATH)


check("a broken record costs the successor its block, not its identity prompt",
      t5_identity_survives_a_broken_record)

# ── §10 best-effort boundary ───────────────────────────────────────────────
print("\n§10 a record that cannot be built never costs the boundary")


def t10_raises():
    slug, nid = make_org("besteffort")
    seed_session(slug, nid)
    real = handoff.capture
    handoff.capture = lambda **kw: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        org, r, dst = cross(slug, nid)
    finally:
        handoff.capture = real
    sd = supervisor.scratch_dir(slug, nid)
    assert dst and os.path.isfile(dst), "the transcript copy was lost with the record"
    assert r.get("bearer") and org.node(nid)["generation"] >= 1, r
    assert not any(x.startswith("handoff-g") for x in os.listdir(sd)), os.listdir(sd)
    assert _staging(sd) == []


check("capture raising leaves the split + transcript copy intact and publishes nothing",
      t10_raises)


def t10_unverifiable():
    slug, nid = make_org("unverifiable")
    seed_session(slug, nid)
    real = handoff.verify

    def _never(art, lines, **kw):
        return ["forced verify failure"]
    handoff.verify = _never
    try:
        org, r, dst = cross(slug, nid)
    finally:
        handoff.verify = real
    sd = supervisor.scratch_dir(slug, nid)
    assert dst and os.path.isfile(dst)
    assert not any(x.startswith("handoff-g") for x in os.listdir(sd)), os.listdir(sd)


check("a record that does not verify is not published, and the boundary still succeeds",
      t10_unverifiable)


def t10_no_transcript():
    """No session journal at all: the export finds nothing to copy, so there
    is nothing to publish either — and nothing raises."""
    slug, nid = make_org("no-transcript")
    with store.DOC_LOCK:
        o = store.load_org(slug)
        o.node(nid)["session_id"] = new_sid()   # never journalled
        o.node(nid).pop("session_unrun", None)
        store.save_org(o)
    org, r, dst = cross(slug, nid)
    sd = supervisor.scratch_dir(slug, nid)
    assert dst is None, dst
    assert not any(x.startswith("handoff-g") for x in os.listdir(sd)), os.listdir(sd)
    assert r.get("bearer"), r


check("a seat with no transcript on disk crosses cleanly and publishes nothing",
      t10_no_transcript)


def t10_cost():
    """A measurement, not a bound: publication runs inside the caller's
    DOC_LOCK window, so its cost is charged to the boundary."""
    slug, nid = make_org("cost")
    big = list(RECS)
    while len(big) < 4000:
        big += RECS
    seed_session(slug, nid, big)
    t0 = time.perf_counter()
    org, r, dst = cross(slug, nid)
    dt = time.perf_counter() - t0
    sd = supervisor.scratch_dir(slug, nid)
    assert handoff.read_generation(sd, int(org.node(nid)["generation"]) - 1)
    OBS["cost"] = {"transcript_lines": len(big),
                   "transcript_bytes": os.path.getsize(dst),
                   "switch_export_publish_seconds": round(dt, 3),
                   "note": "idle test machine, inside DOC_LOCK; a measurement, not a bound"}
    print(f"  {len(big)} lines / {os.path.getsize(dst)} bytes: {dt:.3f} s")


check("cost of a boundary on a large transcript is measured (not asserted as a bound)",
      t10_cost)

# ── §11 mutants ────────────────────────────────────────────────────────────
MUTANTS = {
    "keep_thinking": "excluded content absent",
    "elevate_role": "excluded content absent",
    "leak_outside_grant": "excluded content absent",
    "claim_continuity": "excluded content absent",
    "pair_by_id_only": "repeated tool_use id",
    "truncated_thinking": "truncated leakage",
    "admit_unknown_blocks": "excluded by the",
    "reader_trusts_entry_shapes": "malformed-but-valid-JSON",
    "reason_guessed": "boundary reason is the door",
    "memo_key_transcript_only": "forgery rejected",
    "memo_key_claimed_hash": "altered same-length transcript",
    "nonatomic_publish": "leave NOTHING",
    "read_ignores_manifest": "refuses a manifest-less",
    "flag_ignored": "byte-identical to main",
}

if not MUTANT:
    print("\n§11 mutants of the shipped code — each must turn a NAMED check red")
    for m, want in MUTANTS.items():
        def tm(m=m, want=want):
            r = subprocess.run([sys.executable, os.path.abspath(__file__)],
                               env={**os.environ, "HANDOFF_MUTANT": m},
                               capture_output=True, text=True, timeout=1800)
            assert r.returncode != 0, f"mutant {m} was NOT rejected (suite passed)"
            failed = [ln.split("FAIL", 1)[1].strip()
                      for ln in r.stdout.splitlines() if ln.strip().startswith("FAIL")]
            assert any(want in f for f in failed), \
                f"mutant {m} failed the wrong checks: {failed}"
            OBS.setdefault("mutants", {})[m] = failed
        check(f"mutant {m} turns a check red ({want!r})", tm)

# ═══════════════════════════════════════════════════════════════════════════
if os.environ.get("HANDOFF_EVIDENCE") and not MUTANT:
    with open(os.environ["HANDOFF_EVIDENCE"], "w", encoding="utf-8") as f:
        json.dump(OBS, f, indent=2, ensure_ascii=False, default=str)
print(f"\n{PASS} passed, {len(FAIL)} failed" + (f"  [MUTANT {MUTANT}]" if MUTANT else ""))
for label, tb in FAIL:
    print(f"\n--- {label}\n{tb}")
if not FAIL:
    print(f"ALL {PASS} CHECKS PASS")
sys.exit(1 if FAIL else 0)
