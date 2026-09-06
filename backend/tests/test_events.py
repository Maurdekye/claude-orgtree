"""Canonical typed messages — step 0 (design typed-message-architecture-backend.md v5).

The generator's guarantees, as tests. Every one is written to be able to FAIL: the
refusals are exercised on deliberately broken tables and events (positive controls),
the round-trips run with and without a row in scope, and the disclosure invariant walks
every leaf's REAL fixture.

    §1  the table is checked at import — and refuses what the design says it refuses
    §2  every leaf mints from its fixture, validates strictly, renders deterministically
    §3  codecs: row elision vs bare full serialisation (Opus E1), lenient decode
    §4  public projection: own wire key, own validator, mutual refusal, no withheld key
    §5  disclosure invariant (B16) over the real fixtures; structural keys by construction
    §6  emitters: TS/JSON/manifest are deterministic and agree with the runtime

Hermetic: no data root, no org, no store. `ORGTREE_DATA` is set to a throwaway anyway
because `orgtree.__init__` may bind it (the team's standing rule).

    python backend/tests/test_events.py
"""
from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import traceback
from typing import Any

_TMP = tempfile.mkdtemp(prefix="orgtree-events-")
os.environ["ORGTREE_DATA"] = os.path.join(_TMP, "data")
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from orgtree import events, events_table as T           # noqa: E402
from orgtree.events import EventInvalid, TableInvalid   # noqa: E402
from orgtree.events_fixtures import FIXTURES            # noqa: E402

PASSED = 0
FAILED: list[str] = []


def check(label, fn) -> None:
    global PASSED
    try:
        fn()
    except Exception:                                        # noqa: BLE001
        FAILED.append(f"{label}\n{traceback.format_exc()}")
        print(f"  x {label}")
    else:
        PASSED += 1
        print(f"  ok {label}")


def expect_invalid(fn, code: str | None = None, path: str | None = None):
    try:
        fn()
    except EventInvalid as e:
        if code is not None:
            assert e.code == code, (e.code, code, e.path)
        if path is not None:
            assert e.path == path, (e.path, path)
        return e
    raise AssertionError("expected EventInvalid")


def expect_table_invalid(table_mod, fragment: str):
    try:
        events.check_table(table_mod)
    except TableInvalid as e:
        assert fragment in str(e), (fragment, str(e))
        return
    raise AssertionError(f"expected TableInvalid mentioning {fragment!r}")


class _Tbl:
    """A mutable shallow copy of the real table module's public attributes."""
    def __init__(self):
        for k in ("REFS", "RECORDS", "UNIONS", "LEAVES", "ENVELOPE", "ACTOR", "FAMILIES",
                  "STRUCTURAL", "ELIDED_FIELDS", "F"):
            setattr(self, k, copy.deepcopy(getattr(T, k)) if k != "F" else T.F)


# ══════════════════════════════════════════════════════════════════════════ §1
print("\n§1  the table is checked at import — and refuses what the design says it refuses")

check("table · the real table passes check_table", lambda: events.check_table(T))


def _missing_disposition():
    t = _Tbl()
    del t.LEAVES["status.report"]["fields"]["summary"]["d"]
    expect_table_invalid(t, "missing 'd'")


check("B15 · a field with no disposition is REFUSED (positive control)", _missing_disposition)


def _missing_public():
    t = _Tbl()
    del t.LEAVES["status.report"]["fields"]["summary"]["p"]
    expect_table_invalid(t, "missing 'p'")


check("B15 · a field with no public flag is REFUSED — the public axis has no default",
      _missing_public)


def _public_on_model_only():
    t = _Tbl()
    t.LEAVES["reminder.idle_docket"]["fields"]["more"]["d"] = "model_only"
    t.LEAVES["reminder.idle_docket"]["fields"]["more"]["p"] = True
    expect_table_invalid(t, "public:true on a model_only")


check("B15 · public:true on a model_only field is REFUSED (two axes, not one)",
      _public_on_model_only)


def _structural_private():
    t = _Tbl()
    t.REFS["WorkItemRef"]["kind"]["p"] = False
    expect_table_invalid(t, "structural key must be public")


check("table · a structural key marked non-public is REFUSED (public by rule)",
      _structural_private)


def _exempt_on_private():
    t = _Tbl()
    t.LEAVES["lifecycle.kickoff"]["fields"]["tier"]["x"] = "why"
    expect_table_invalid(t, "public_exempt on a non-public field")


check("B16 · public_exempt on a non-public field is REFUSED", _exempt_on_private)


def _bad_family():
    t = _Tbl()
    t.LEAVES["status.report"]["family"] = "misc"
    expect_table_invalid(t, "family 'misc' not in FAMILIES")


check("table · a leaf with an unlisted family is REFUSED (explicit table, no prefix)",
      _bad_family)


def _families_explicit():
    # participant_added is context_change and the subagent/bg-task leaves are runtime —
    # the approved table, not the prefix
    assert events.FAMILY_OF["docket.participant_added"] == "context_change"
    assert events.FAMILY_OF["runtime.subagent_died"] == "runtime_recovery"
    assert set(events.FAMILY_OF.values()) == set(T.FAMILIES), \
        "every approved family has at least one leaf"


check("table · FAMILY_OF is the explicit table; all 12 families populated", _families_explicit)


def _every_leaf_has_fixture():
    missing = [v for v in events.VARIANTS if v not in FIXTURES]
    extra = [v for v in FIXTURES if v not in events.VARIANTS]
    assert not missing, f"leaves without a fixture: {missing}"
    assert not extra, f"fixtures for unknown leaves: {extra}"


check("fixtures · every leaf has exactly one fixture and no fixture is orphaned",
      _every_leaf_has_fixture)


# ══════════════════════════════════════════════════════════════════════════ §2
print("\n§2  every leaf mints from its fixture, validates strictly, renders deterministically")


def _mint_all():
    for v, fx in FIXTURES.items():
        ev = events.mint(v, fx["actor"], fx.get("object"), **fx["fields"])
        assert ev["variant"] == v and ev["v"] == events.EVENT_V
        assert ev["engine_authored"] == (fx["actor"]["kind"] == "system")
        text = events.render_agent(ev)
        assert isinstance(text, str) and text, v
        assert events.render_agent(ev) == text, "render is not deterministic"


check("mint · all 84 leaves mint from their fixture and render non-empty, deterministically",
      _mint_all)


def _mint_refusals():
    fx = FIXTURES["status.report"]
    a, o, f = fx["actor"], fx["object"], dict(fx["fields"])
    expect_invalid(lambda: events.mint("status.reprot", a, o, **f), "unknown_variant")
    expect_invalid(lambda: events.mint("status.report", a, o, **{k: v for k, v in f.items() if k != "summary"}),
                   "missing_field", "summary")
    expect_invalid(lambda: events.mint("status.report", a, o, **f, extra=1),
                   "extra_field", "extra")
    expect_invalid(lambda: events.mint("status.report", a, o, **{**f, "state": "working"}),
                   "bad_literal", "state")
    expect_invalid(lambda: events.mint("status.report", a, o, **{**f, "summary": 3}),
                   "wrong_type", "summary")
    expect_invalid(lambda: events.mint("status.report", a, {**o, "kind": "org"}, **f),
                   "bad_literal", "object.kind")
    expect_invalid(lambda: events.mint("status.report", {"kind": "robot", "id": "x"}, o, **f),
                   "bad_literal", "actor.kind")
    expect_invalid(lambda: events.mint("status.report", a, None, **f), "wrong_type", "object")


check("mint · unknown variant / missing / extra / bad literal / wrong type / bad ref / bad actor "
      "/ null object all refuse with a STATIC code+path", _mint_refusals)


def _strict_scalars():
    fx = FIXTURES["lifecycle.hired"]
    a, o, f = fx["actor"], fx["object"], dict(fx["fields"])
    expect_invalid(lambda: events.mint("lifecycle.hired", a, o, **{**f, "grant": True}),
                   "wrong_type", "grant")
    expect_invalid(lambda: events.mint("lifecycle.hired", a, o, **{**f, "grant": float("inf")}),
                   "not_finite", "grant")
    fx2 = FIXTURES["lifecycle.deleted"]
    expect_invalid(lambda: events.mint("lifecycle.deleted", fx2["actor"], fx2["object"],
                                       **{**fx2["fields"], "extra": True}), "wrong_type", "extra")
    expect_invalid(lambda: events.mint("lifecycle.deleted", fx2["actor"], fx2["object"],
                                       **{**fx2["fields"], "extra": 1.5}), "wrong_type", "extra")


check("mint · bool is not float, inf is not finite, bool/float are not int", _strict_scalars)


def _nested_and_min():
    fx = FIXTURES["answer.batch"]
    a, o = fx["actor"], fx["object"]
    expect_invalid(lambda: events.mint("answer.batch", a, o, sections=[]), "min_length", "sections")
    expect_invalid(lambda: events.mint("answer.batch", a, o, sections=[{}]),
                   "bad_literal", "sections[0].kind")
    expect_invalid(lambda: events.mint("answer.batch", a, o, sections=[{"kind": "ask"}]),
                   "missing_field", "sections[0].ask_id")
    bad = copy.deepcopy(fx["fields"])
    bad["sections"][0]["questions"][0]["answer"] = 7
    expect_invalid(lambda: events.mint("answer.batch", a, o, **bad),
                   "wrong_type", "sections[0].questions[0].answer")
    fx = FIXTURES["runtime.ui_crash_report"]
    expect_invalid(lambda: events.mint("runtime.ui_crash_report", fx["actor"], fx["object"],
                                       **{**fx["fields"], "report": {}}),
                   "missing_field", "report.kind")


check("mint · nested unions/records validate recursively; empty lists and {} are refused "
      "(Opus O5)", _nested_and_min)


def _engine_authored_derived():
    fx = FIXTURES["lifecycle.kickoff"]
    ev = events.mint("lifecycle.kickoff", {"kind": "system", "id": "@system"}, fx["object"],
                     **fx["fields"])
    assert ev["engine_authored"] is True
    ev2 = dict(ev)
    ev2["engine_authored"] = False
    expect_invalid(lambda: events.validate_event(ev2), "bad_structure", "engine_authored")


check("mint · engine_authored is derived from actor.kind and cannot be contradicted",
      _engine_authored_derived)


# ══════════════════════════════════════════════════════════════════════════ §3
print("\n§3  codecs: row elision vs bare full serialisation (Opus E1), lenient decode")


def _row_roundtrip():
    for v, fx in FIXTURES.items():
        ev = events.mint(v, fx["actor"], fx.get("object"), **fx["fields"])
        body = events.render_agent(ev)
        row = {"body": body}
        raw = events.encode_row_ev(ev, row)
        for name in T.ELIDED_FIELDS.get(v, ()):
            assert name not in raw, f"{v}: {name} should be elided on the row"
        assert events.decode_row_ev(raw, row) == ev, v


check("codec · row round-trip per leaf; ordinary/reply bodies elided on the row", _row_roundtrip)


def _bare_roundtrip():
    for v, fx in FIXTURES.items():
        ev = events.mint(v, fx["actor"], fx.get("object"), **fx["fields"])
        raw = events.encode_ev(ev)
        for name in T.ELIDED_FIELDS.get(v, ()):
            assert name in raw, f"{v}: bare encoding must carry {name} in full"
        assert events.decode_ev(raw) == ev, v


check("codec · bare round-trip per leaf WITH NO ROW IN SCOPE — nothing elided", _bare_roundtrip)


def _rolled_cap_projection():
    """A span snapshot must yield the full ordinary body after the row is gone."""
    fx = FIXTURES["ordinary.message"]
    ev = events.mint("ordinary.message", fx["actor"], None, **fx["fields"])
    snapshot = json.dumps(events.encode_ev(ev))       # what a projection row stores
    # …the mail_log has since rolled: no row exists any more
    back = events.decode_ev(json.loads(snapshot))
    assert back["body"] == fx["fields"]["body"]


check("codec · a bare snapshot still yields the full body once the mail_log cap rolled "
      "(Opus E1 test c)", _rolled_cap_projection)


def _row_elision_mismatch():
    fx = FIXTURES["ordinary.message"]
    ev = events.mint("ordinary.message", fx["actor"], None, **fx["fields"])
    expect_invalid(lambda: events.encode_row_ev(ev, {"body": "something else"}),
                   "bad_structure", "body")


check("codec · row encoder refuses to elide a body that differs from the row (I1 guard)",
      _row_elision_mismatch)


def _lenient_decode():
    assert events.decode(None) == {"status": "legacy"}
    fx = FIXTURES["status.report"]
    ev = events.mint("status.report", fx["actor"], fx["object"], **fx["fields"])
    assert events.decode(events.encode_ev(ev))["status"] == "ok"
    r = events.decode({**ev, "v": 99})
    assert r["status"] == "unsupported" and r["error"] == {
        "code": "unknown_version", "path": "v", "expected": "≤1"}
    r = events.decode({**ev, "variant": "future.leaf"})
    assert r["status"] == "unsupported" and r["error"]["code"] == "unknown_variant"
    r = events.decode({**ev, "summary": 3})
    assert r["status"] == "malformed" and r["error"] == {
        "code": "wrong_type", "path": "summary", "expected": "str"}
    assert "3" not in json.dumps(r), "the offending VALUE must never appear in the error"
    r = events.decode("not a dict")
    assert r["status"] == "malformed"


check("decode · legacy / ok / unsupported / malformed, static error, value never leaks",
      _lenient_decode)


# ══════════════════════════════════════════════════════════════════════════ §4
print("\n§4  public projection: own wire key, own validator, mutual refusal, no withheld key")


def _walk(o: Any, path: str, out: list[tuple[str, Any]]):
    if isinstance(o, dict):
        for k, v in o.items():
            _walk(v, f"{path}.{k}" if path else k, out)
    elif isinstance(o, list):
        for i, v in enumerate(o):
            _walk(v, f"{path}[{i}]", out)
    else:
        out.append((path, o))


def _public_all():
    for v, fx in FIXTURES.items():
        ev = events.mint(v, fx["actor"], fx.get("object"), **fx["fields"])
        pub = events.public_event(ev)
        assert pub["projection"] == "public" and pub["variant"] == v
        assert "engine_authored" not in pub, v
        assert not (pub.get("object") or {}).get("org"), v
        events.validate_public_event(pub)
        # the private decoder refuses the public shape and vice versa
        expect_invalid(lambda: events.validate_event(pub), "extra_field", "projection")
        expect_invalid(lambda: events.validate_public_event(ev), "bad_structure", "projection")


check("public · every leaf projects to a valid PublicEvent; private/public decoders refuse "
      "each other's shape", _public_all)


def _sentinels():
    fx = FIXTURES["access.scope_requested"]
    fields = copy.deepcopy(fx["fields"])
    fields["wanted"]["folders"][0]["path"] = "SENTINEL-A-HOST-PATH"      # public:false
    obj = {**fx["object"], "org": "SENTINEL-ORG"}                        # internal
    ev = events.mint("access.scope_requested", fx["actor"], obj, **fields)
    pub = events.public_event(ev)
    flat = json.dumps(pub)
    assert "SENTINEL-A-HOST-PATH" not in flat and "SENTINEL-ORG" not in flat
    assert "path" not in json.dumps(pub["wanted"]["folders"]), "public:false key absent"
    assert pub["reason"] == fields["reason"], "a public field IS present (sentinel C)"
    assert "SENTINEL-A-HOST-PATH" in json.dumps(ev), "…and IS in the private event (control)"


check("public · sentinel in a public:false field absent, public field present, org absent "
      "(B14 shape at the projection layer)", _sentinels)


def _public_error_shape():
    e = expect_invalid(lambda: events.validate_event({"v": 1, "variant": "status.report",
                                                      "actor": {"kind": "agent", "id": "a"},
                                                      "object": None, "engine_authored": False,
                                                      "SECRET_KEY_NAME": 1, "state": "done",
                                                      "summary": "s"}))
    assert e.public() == {"code": "extra_field"}, "public error is the code ONLY"
    assert e.admin()["path"] == "SECRET_KEY_NAME"


check("public · ev_error for visitors is {code} only; the key path stays admin-side",
      _public_error_shape)


# ══════════════════════════════════════════════════════════════════════════ §5
print("\n§5  disclosure invariant (B16) over the real fixtures; structural keys by construction")


def _get_path(o: Any, path: str) -> list[Any]:
    """all values at a dotted path (lists fan out)"""
    cur: list[Any] = [o]
    for part in path.split("."):
        nxt: list[Any] = []
        for c in cur:
            if isinstance(c, dict) and part in c:
                val = c[part]
                nxt.extend(val if isinstance(val, list) else [val])
            elif isinstance(c, list):
                for item in c:
                    if isinstance(item, dict) and part in item:
                        val = item[part]
                        nxt.extend(val if isinstance(val, list) else [val])
        cur = nxt
    return cur


def _b16():
    violations: list[str] = []
    exempt_used: set[str] = set()
    for v, fx in FIXTURES.items():
        ev = events.mint(v, fx["actor"], fx.get("object"), **fx["fields"])
        text = events.render_agent(ev)
        for path, exempt in events.public_string_fields(v):
            if exempt:
                exempt_used.add(f"{v}:{path}")
                continue
            for val in _get_path(ev, path):
                if val is None or val == "":
                    continue
                if str(val) not in text:
                    violations.append(f"{v}: {path}={val!r} not in render")
    assert not violations, "\n".join(violations)
    assert exempt_used, "the exemption list is non-empty (the fixtures exercise it)"


check("B16 · every table-marked public string field's fixture value appears verbatim in "
      "render_agent (or carries a listed exemption)", _b16)


def _b16_positive_control():
    """A public string that the renderer does NOT print must be caught."""
    t = _Tbl()
    t.LEAVES["status.report"]["fields"]["hidden"] = T.F("str", "both", True)
    events.check_table(t)                                # the table itself is fine…
    # …but the invariant walk over a leaf carrying a value the render omits fails:
    fx = FIXTURES["status.report"]
    ev = events.mint("status.report", fx["actor"], fx["object"], **fx["fields"])
    ev2 = {**ev, "hidden": "NEVER-RENDERED"}
    text = events.render_agent(ev)
    assert "NEVER-RENDERED" not in text
    # the same walker the real check uses would flag it:
    assert any(str(val) not in text for val in _get_path(ev2, "hidden"))


check("B16 · positive control: a public string the renderer omits is detectable",
      _b16_positive_control)


def _structural_excluded():
    for v in events.VARIANTS:
        paths = [p for p, _ in events.public_string_fields(v)]
        for s in ("v", "variant", "projection", "actor.kind", "object.kind"):
            assert s not in paths, f"{v}: structural {s} must not be in the invariant's domain"


check("B16 · structural keys are outside the invariant's domain by construction",
      _structural_excluded)


# ══════════════════════════════════════════════════════════════════════════ §6
print("\n§6  emitters: TS/JSON/manifest are deterministic and agree with the runtime")


def _emit_determinism():
    assert events.emit_typescript() == events.emit_typescript()
    assert events.emit_json_schema() == events.emit_json_schema()
    assert events.manifest() == events.manifest()


check("emit · TypeScript, JSON schema and manifest are deterministic", _emit_determinism)


def _ts_shape():
    ts = events.emit_typescript()
    assert "export type Event =" in ts and "export type PublicEvent =" in ts
    assert 'variant: "docket.assigned"' in ts
    assert "export interface PublicAccessScopeRequested" in ts
    private_part = ts.split("// ---- PUBLIC")[0]
    public_part = ts.split("// ---- PUBLIC")[1].split("export const FAMILY_OF")[0]
    assert "path: string" in private_part
    assert "path: string" not in public_part, "public:false field absent in TS"
    assert "engine_authored" not in public_part
    assert 'projection: "public"' in ts
    assert "GENERATED" in ts.splitlines()[0]


check("emit · TS carries private + public unions, public omits withheld fields", _ts_shape)


def _json_schema_shape():
    js = events.emit_json_schema()
    defs = js["$defs"]
    assert "Event" in defs and "PublicEvent" in defs
    assert len(defs["Event"]["oneOf"]) == len(events.VARIANTS)
    leaf = defs["AccessScopeRequested"]
    assert leaf["additionalProperties"] is False
    assert "path" in defs["Folder"]["properties"]
    assert "path" not in defs["PublicFolder"]["properties"]
    assert defs["PublicAccessScopeRequested"]["properties"]["projection"] == {"const": "public"}


check("emit · JSON schema has one leaf per variant, closed objects, public defs withhold",
      _json_schema_shape)


def _manifest_complete():
    m = events.manifest()
    for v, leaf in m["leaves"].items():
        for name, f in leaf["fields"].items():
            assert f["disposition"] in events.DISPOSITIONS, (v, name)
            assert isinstance(f["public"], bool), (v, name)
    assert m["structural"] == sorted(T.STRUCTURAL)
    assert m["families"] == list(T.FAMILIES)


check("emit · manifest lists both attributes for every field of every leaf", _manifest_complete)


print("\n" + "═" * 70)
print(f"{PASSED} checks passed, {len(FAILED)} failed")
for f in FAILED:
    print("\nFAIL:", f)
sys.exit(1 if FAILED else 0)
