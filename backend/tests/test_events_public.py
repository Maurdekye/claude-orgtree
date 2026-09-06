"""B14 — the public (kiosk) projection of typed messages, tested BEHAVIOURALLY
(design typed-message-architecture-backend.md v5 §6, Opus E2/P1, root 18:38).

Three sentinels are planted in one org:
    A  in a `public:false` field   (access.scope_requested.wanted.folders[].path)
    B  in an already-visible BODY  (an authored ordinary message)
    C  in a `public:true` field    (docket.assigned.objective)
plus a malformed typed row carrying sentinel A under an UNKNOWN key, and one legacy row.

Then EVERY GET route in app.routes whose path is org-scoped is driven through the
PublicGateway with the org's kiosk token, and every 200 JSON body is deep-walked:
    · sentinel A, the keys `ev`/`ev_raw`, and every `public:false` key path from the
      manifest must appear NOWHERE  (negative)
    · sentinel B must appear, byte-identical to the admin response's body  (unchanged)
    · sentinel C must appear inside some `ev_public` at its path, and that `ev_public`
      must decode as a valid PublicEvent  (POSITIVE — an empty projection fails)
    · the admin drive of the same routes must show sentinel A  (fixture-reach control)
    · `ev_error` publicly is `{code}` only; the unknown key name is not in any body
A new route that returns rows fails here by leaking, not by being unlisted.

    python backend/tests/test_events_public.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import traceback
from typing import Any

_TMP = tempfile.mkdtemp(prefix="orgtree-evpublic-")
os.environ["ORGTREE_DATA"] = os.path.join(_TMP, "data")
os.environ.pop("ORGTREE_WARM", None)
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w", encoding="utf-8") as _f:
    _f.write('{"net_hub_address":"http://127.0.0.1:9"}')
os.environ["USERPROFILE"] = os.environ["HOME"] = os.path.join(_TMP, "home")
os.makedirs(os.environ["HOME"], exist_ok=True)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from fastapi.routing import APIRoute                          # noqa: E402
from fastapi.testclient import TestClient                     # noqa: E402
from orgtree import api, events, store, supervisor            # noqa: E402
from orgtree.ledger import SYSTEM, USER, actor_of             # noqa: E402

assert store.DATA_ROOT.startswith(_TMP), store.DATA_ROOT
supervisor.send_message = lambda *a, **k: {"accepted": True, "queued": 0}
api.supervisor.send_message = supervisor.send_message

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


SENT_A = "SENTINEL-A-HOST-PATH-Q7"
SENT_B = "SENTINEL-B-VISIBLE-BODY-Q7"
SENT_C = "SENTINEL-C-PUBLIC-FIELD-Q7"
UNKNOWN_KEY = "SENTINEL_UNKNOWN_KEY_Q7"
TOKEN = "tok_" + "p" * 24

admin = TestClient(api.app)
public = TestClient(api.PublicGateway(api.app))


def build_org() -> str:
    org = store.create_org("public-probe", [])
    org.hire(USER, None, "opus", 20, "boss")
    org.hire(USER, "boss", "haiku", 5, "kid")
    slug = org.d["slug"]
    # B: authored body, typed ordinary
    org.post_mail(USER, "boss", f"hello {SENT_B} world", "message", typed=True)
    # A: public:false field of a system leaf, mailed to boss
    ev_a = events.mint("access.scope_requested", actor_of("kid"),
                       {"kind": "scope_request", "org": slug, "id": "sr1", "node": "kid"},
                       items=["folder C:/x (rw)"], reason="need it",
                       wanted={"folders": [{"path": SENT_A, "mode": "rw"}],
                               "tools": {"bash": None, "web": None, "edit": None,
                                         "subagents": None, "mcp": None},
                               "permission_mode": None, "org_visibility": None})
    org.post_event("kid", "boss", ev_a, kind="request")
    # C: public:true field of a system leaf
    ev_c = events.mint("docket.assigned", actor_of(USER),
                       {"kind": "work_item", "org": slug, "slug": "wi-1", "title": "Item"},
                       owner="boss", previous_owner=None, assigner=USER, status="open",
                       objective=f"objective {SENT_C}", done_so_far=[], working_on_next=[])
    org.post_event(USER, "boss", ev_c, kind="request")
    # typed notice + user-inbox typed row + engine row, so every surface has ev rows
    org._notify_ev(["boss"], events.mint("lifecycle.renamed", actor_of(USER),
                                         {"kind": "node", "org": slug, "id": "kid",
                                          "name": "kid", "generation": 0},
                                         old="kid", new="kid2", by=USER))
    org.to_user_inbox({"from": SYSTEM, "kind": "notice", "at": "2026-09-06T00:00:00Z",
                       "body": ""},
                      events.mint("runtime.token_expiry", actor_of(SYSTEM),
                                  {"kind": "org", "org": slug}, days=2.0))
    org.append_system_mail("boss", events.mint(
        "runtime.turn_failed_terminal", actor_of(SYSTEM),
        {"kind": "session", "org": slug, "node": "kid", "session_id": "SESSION-SECRET-Q7"},
        door="spawn", err="boom"))
    # malformed typed row: unknown variant + sentinel A under an unknown key
    org.d["mail"]["boss"].append({"id": "malformed01", "from": "@system", "kind": "message",
                                  "body": "malformed body", "at": "2026-09-06T00:00:01Z",
                                  "relationship": "the orgtree engine",
                                  "ev": {"v": 1, "variant": "future.leaf",
                                         UNKNOWN_KEY: SENT_A}})
    # legacy row
    org.post_mail("boss", "kid", "legacy plain", "message")
    org.d["kiosk"] = {"enabled": True, "token": TOKEN}
    store.save_org(org)
    api._token_cache["at"] = 0.0
    return slug


SLUG = build_org()


def org_scoped_get_paths(slug: str) -> list[str]:
    """every GET route whose path is org-scoped, instantiated for this org."""
    out: list[str] = []
    for r in api.app.routes:
        if not isinstance(r, APIRoute) or "GET" not in r.methods:
            continue
        p = r.path
        if not p.startswith("/api/orgs/{slug}"):
            continue
        p = p.replace("{slug}", slug).replace("{nid}", "boss").replace("{box}", "node") \
             .replace("{mid}", "x").replace("{wid}", "x").replace("{did}", "x") \
             .replace("{aid}", "x").replace("{tool_use_id}", "x").replace("{peer}", "x")
        if "{" in p:
            continue
        out.append(p)
    return sorted(set(out))


def walk(o: Any, path: str, out: list[tuple[str, Any]]):
    if isinstance(o, dict):
        for k, v in o.items():
            out.append((f"{path}.{k}" if path else k, "<key>"))
            walk(v, f"{path}.{k}" if path else k, out)
    elif isinstance(o, list):
        for i, v in enumerate(o):
            walk(v, f"{path}[{i}]", out)
    else:
        out.append((path, o))


def drive(client: TestClient, prefix: str) -> dict[str, Any]:
    bodies: dict[str, Any] = {}
    for p in org_scoped_get_paths(SLUG):
        q = "?node=boss&path=." if p.endswith(("/mail/node/x", "/scratch")) else ""
        r = client.get(prefix + p + q)
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("application/json"):
            bodies[p] = r.json()
    return bodies


PUB = drive(public, f"/k/{TOKEN}")
ADM = drive(admin, "")


def _routes_seen():
    assert len(PUB) >= 5, f"public drive reached only {sorted(PUB)}"
    for must in (f"/api/orgs/{SLUG}/nodes/boss/inbox", f"/api/orgs/{SLUG}/nodes/boss/chat",
                 f"/api/orgs/{SLUG}/inbox", f"/api/orgs/{SLUG}/nodes/boss/history"):
        assert must in PUB, f"{must} not reachable publicly (gateway or route changed?)"


check("walk · the public drive reaches the mail-bearing routes", _routes_seen)


def _negative():
    for p, body in PUB.items():
        flat = json.dumps(body, ensure_ascii=False)
        assert SENT_A not in flat, f"{p}: sentinel A leaked"
        assert "SESSION-SECRET-Q7" not in flat, f"{p}: session id leaked"
        assert UNKNOWN_KEY not in flat, f"{p}: unknown key name leaked"
        leaves: list[tuple[str, Any]] = []
        walk(body, "", leaves)
        for path, v in leaves:
            last = path.rsplit(".", 1)[-1].split("[")[0]
            assert last not in ("ev", "ev_raw"), f"{p}: {path} present publicly"
        # PATH-QUALIFIED (design §6 mechanic v): every ev_public must satisfy the
        # PublicEvent schema, which is closed — a public:false key anywhere inside it
        # is an extra_field refusal at its exact path, never a global key-name ban
        for _path, container in _containers(body, "ev_public"):
            events.validate_public_event(container)


def _containers(o: Any, key: str, path: str = "") -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    if isinstance(o, dict):
        for k, v in o.items():
            if k == key:
                out.append((f"{path}.{k}" if path else k, v))
            else:
                out += _containers(v, key, f"{path}.{k}" if path else k)
    elif isinstance(o, list):
        for i, v in enumerate(o):
            out += _containers(v, key, f"{path}[{i}]")
    return out


check("B14 · sentinel A, session id, unknown key, `ev`/`ev_raw` and every public:false key "
      "absent from every public response", _negative)


def _positive_c():
    found = 0
    for p, body in PUB.items():
        leaves: list[tuple[str, Any]] = []
        walk(body, "", leaves)
        for path, v in leaves:
            if v == f"objective {SENT_C}" and ".ev_public." in path and path.endswith(".objective"):
                found += 1
                # the enclosing ev_public must be a valid PublicEvent
                container = body
                for part in re.findall(r"[^.\[\]]+|\[\d+\]", path):
                    if part.startswith("["):
                        container = container[int(part[1:-1])]
                    elif part == "ev_public":
                        container = container[part]
                        events.validate_public_event(container)
                        break
                    else:
                        container = container[part]
    assert found >= 2, f"sentinel C found in {found} ev_public field(s) — projection empty?"


check("B14 · sentinel C present inside a valid ev_public at its path (positive control)",
      _positive_c)


def _body_unchanged():
    seen = 0
    for p in PUB:
        if p not in ADM:
            continue
        pub_bodies = [v for path, v in _leaves(PUB[p]) if path.endswith(".body")]
        adm_bodies = [v for path, v in _leaves(ADM[p]) if path.endswith(".body")]
        if any(isinstance(b, str) and SENT_B in b for b in adm_bodies):
            seen += 1
            assert [b for b in pub_bodies if isinstance(b, str) and SENT_B in b] == \
                   [b for b in adm_bodies if isinstance(b, str) and SENT_B in b], p
    assert seen >= 1, "sentinel B body never reached a surface"


def _leaves(o):
    out: list[tuple[str, Any]] = []
    walk(o, "", out)
    return out


check("B14 · sentinel B body byte-identical public vs admin (no scrubbing, no rewriting)",
      _body_unchanged)


def _admin_control():
    flat = json.dumps(ADM, ensure_ascii=False)
    assert SENT_A in flat, "admin must see sentinel A (fixture-reach control)"
    assert "SESSION-SECRET-Q7" in flat
    assert UNKNOWN_KEY in flat, "admin sees ev_raw with the unknown key"
    inbox = ADM[f"/api/orgs/{SLUG}/nodes/boss/inbox"]
    rows = inbox["pending"] + inbox["delivered"]
    typed = [r for r in rows if "ev" in r]
    assert typed, "admin rows carry ev"
    for r in typed:
        events.validate_event(r["ev"])
        assert "body" in r["ev"] if r["ev"]["variant"].startswith("ordinary.") else True, \
            "wire ev is FULL (no row elision on the wire)"
    bad = [r for r in rows if r.get("id") == "malformed01"]
    assert bad and bad[0]["ev_error"]["code"] == "unknown_variant" and "ev_raw" in bad[0]
    legacy = [r for r in rows if r.get("body") == "legacy plain"]
    assert legacy == [] or "ev" not in legacy[0]


check("control · admin sees sentinel A, full wire events (ordinary body present), ev_raw + "
      "static ev_error on the malformed row, legacy rows without ev", _admin_control)


def _public_error_shape():
    inbox = PUB[f"/api/orgs/{SLUG}/nodes/boss/inbox"]
    rows = inbox["pending"] + inbox["delivered"]
    bad = [r for r in rows if r.get("id") == "malformed01"]
    assert bad and bad[0]["ev_error"] == {"code": "unknown_variant"}, bad
    assert "ev_raw" not in bad[0] and "ev" not in bad[0] and "ev_public" not in bad[0]


check("B14 · public ev_error is {code} only on the malformed row", _public_error_shape)


def _decoder_profiles():
    inbox = PUB[f"/api/orgs/{SLUG}/nodes/boss/inbox"]
    for r in inbox["pending"] + inbox["delivered"]:
        if "ev_public" in r:
            events.validate_public_event(r["ev_public"])
            try:
                events.validate_event(r["ev_public"])
                raise AssertionError("private decoder accepted a public shape")
            except events.EventInvalid:
                pass


check("B14 · every public row decodes as PublicEvent and is refused by the private decoder",
      _decoder_profiles)


print("\n" + "═" * 70)
print(f"{PASSED} checks passed, {len(FAILED)} failed")
for f in FAILED:
    print("\nFAIL:", f)
sys.exit(1 if FAILED else 0)
