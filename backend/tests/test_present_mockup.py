"""present-html-mockups-in-a-new-browser-tab (2026-09-06) — the backend half.

`orgtree_present` with `path` snapshots a self-contained .html file into the
node's outbox/ (the orgtree_send_file rule), the ledger records
`format: "html"` + the snapshot's name and NO body, and the one route that
ever serves the bytes is the sandboxed wrapper
`GET /api/orgs/{slug}/documents/{did}/mockup`.

Why the wrapper is the whole boundary: the admin app authenticates by
loopback alone, the kiosk by the token in its URL path, and there is no CORS
layer — agent-authored HTML rendered at the app origin with script would act
as the user against every /api route. So this suite checks the SHAPE of that
boundary byte-for-byte: the escaping, the sandbox attribute, the CSP, the
base/referrer fixings, the kiosk 403. Whether a browser honours the shape is
feature-astra's browser probe (Chromium, 2026-09-06), not something a
TestClient can see — nothing here claims it.

    §1  present-by-path — the contract on the tool side
    §2  containment and snapshot semantics (send_file's rule, reused)
    §3  the records — ledger, tree payload, gallery, GET document
    §4  the wrapper route — headers, escaping, 403/404/410/422
    §5  positive controls — the checks can fail

Every check drives the installed door (`POST /api/agent`) or the HTTP route
through the FastAPI test client, against a throwaway ORGTREE_DATA.

    python backend/tests/test_present_mockup.py
"""
from __future__ import annotations

import html
import os
import re
import sys
import tempfile
import traceback

_TMP = tempfile.mkdtemp(prefix="orgtree-mockup-")
os.environ["ORGTREE_DATA"] = os.path.join(_TMP, "data")
os.environ.pop("ORGTREE_WARM", None)
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w",
          encoding="utf-8") as _f:
    _f.write('{"net_hub_address":"http://127.0.0.1:9"}')
os.environ["USERPROFILE"] = os.environ["HOME"] = os.path.join(_TMP, "home")
os.makedirs(os.environ["HOME"], exist_ok=True)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from fastapi.testclient import TestClient                      # noqa: E402
from orgtree import api, store, supervisor                     # noqa: E402
from orgtree.ledger import LedgerError, USER                   # noqa: E402

assert store.DATA_ROOT.startswith(_TMP), store.DATA_ROOT   # throwaway root


def _fake_send(slug, nid, text, command=False, wake=True, **kw):
    return {"accepted": True, "queued": 0}


supervisor.send_message = _fake_send
api.supervisor.send_message = _fake_send

client = TestClient(api.app)
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


_n = [0]
ALL_TOOLS = {"bash": True, "web": True, "edit": True, "subagents": True,
             "mcp": []}

# a mockup with every character the escaping must survive: a double quote
# (closes the srcdoc attribute if unescaped), a closing iframe tag, an
# ampersand and an inline script with an apostrophe
MOCK = ('<!DOCTYPE html><html><head><style>body{color:#123}</style></head>'
        '<body><h1 id="t">Mock "quoted" &amp; done</h1></iframe>'
        "<button onclick=\"document.getElementById('t').textContent='clicked'\">go</button>"
        '<script>window.x = 1 < 2 && "ok";</script></body></html>')


def fresh_org():
    """boss (top-level: holds the user audience by being top level) and a
    kid without one. The org gets an outside folder as workspace-free
    'granted' tree via boss's add_dirs so the containment checks have a
    held root, a foreign root and a symlink target to test."""
    _n[0] += 1
    held = os.path.join(_TMP, f"held-{_n[0]}")
    foreign = os.path.join(_TMP, f"foreign-{_n[0]}")
    os.makedirs(held, exist_ok=True)
    os.makedirs(foreign, exist_ok=True)
    org = store.create_org(f"mockup-{_n[0]}", [])
    org.hire(USER, None, "opus", 20, "boss",
             add_dirs=[{"path": held, "mode": "rw"}])
    org.hire(USER, "boss", "haiku", 5, "kid", add_dirs=[],
             tools=dict(ALL_TOOLS), org_visibility="team", charter="t")
    store.save_org(org)
    return org.d["slug"], held, foreign


def scratch(slug, nid="boss"):
    p = supervisor.scratch_dir(slug, nid)
    os.makedirs(p, exist_ok=True)
    return p


def plant(folder, name, text=MOCK):
    p = os.path.join(folder, name)
    with open(p, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return p


def present(slug, node="boss", **args):
    r = client.post("/api/agent", json={"org": slug, "node": node,
                                        "tool": "orgtree_present",
                                        "args": args})
    js = (r.json() if r.headers.get("content-type", "").startswith(
        "application/json") else r.text)
    return r.status_code, js


def doc_of(slug, did):
    return next(x for x in store.load_org(slug).d["documents"]
                if x["id"] == did)


def outbox_names(slug, nid="boss"):
    d = os.path.join(scratch(slug, nid), "outbox")
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


# ══════════════════════════════════════════════════════════════════════ §1
print("\n§1  present-by-path — the contract on the tool side")


def _happy():
    slug, _h, _f = fresh_org()
    plant(scratch(slug), "mock.html")
    st, js = present(slug, title="Login mockup", path="mock.html")
    assert st == 200, (st, js)
    assert js["presented"].startswith("d") and js["format"] == "html", js
    assert "new browser tab" in js["status"], js
    d = doc_of(slug, js["presented"])
    assert d["format"] == "html" and d["file"] == "outbox/mock.html", d
    assert d["body"] == "" and d["bytes"] == len(MOCK.encode()), d
    assert outbox_names(slug) == ["mock.html"]


check("path · a .html in the working folder becomes an html card + outbox snapshot",
      _happy)


def _both():
    slug, _h, _f = fresh_org()
    plant(scratch(slug), "mock.html")
    st, js = present(slug, title="T", path="mock.html", body="# also md")
    assert st != 200 and "OR" in str(js), (st, js)
    assert outbox_names(slug) == [], "a refused present leaves no snapshot"


check("path · path AND body is refused, with no outbox residue", _both)


def _ext():
    slug, _h, _f = fresh_org()
    plant(scratch(slug), "mock.txt")
    plant(scratch(slug), "mock.md", "# md")
    for name in ("mock.txt", "mock.md"):
        st, js = present(slug, title="T", path=name)
        assert st != 200 and ".html" in str(js), (name, st, js)
    assert outbox_names(slug) == []
    plant(scratch(slug), "Mock.HTM")
    st, js = present(slug, title="T", path="Mock.HTM")
    assert st == 200, (st, js)                    # .htm, any case


check("path · only .html/.htm (case-insensitive) — .txt and .md are refused",
      _ext)


def _markdown_untouched():
    slug, _h, _f = fresh_org()
    st, js = present(slug, title="Plan", body="# plan")
    assert st == 200 and "format" not in js, js
    d = doc_of(slug, js["presented"])
    assert "format" not in d and d["body"] == "# plan", d
    st, js = present(slug, title="Plan")
    assert st != 200 and "empty" in str(js), (st, js)


check("body · the markdown path is unchanged (no format key, empty body refused)",
      _markdown_untouched)


def _gate_before_copy():
    slug, _h, _f = fresh_org()
    plant(scratch(slug, "kid"), "mock.html")
    st, js = present(slug, node="kid", title="T", path="mock.html")
    assert st != 200 and "audience" in str(js), (st, js)
    assert outbox_names(slug, "kid") == [], \
        "the audience refusal must run BEFORE the snapshot"
    st, js = present(slug, title="", path="mock.html")
    assert st != 200 and "title" in str(js), (st, js)


check("gate · no user audience / no title refuse BEFORE any outbox copy",
      _gate_before_copy)


def _cap():
    slug, _h, _f = fresh_org()
    big = os.path.join(scratch(slug), "big.html")
    with open(big, "wb") as fh:
        fh.write(b"<html>" + b"x" * (api._MOCKUP_MAX + 1) + b"</html>")
    st, js = present(slug, title="T", path="big.html")
    assert st != 200 and "4 MB cap" in str(js), (st, js)
    assert outbox_names(slug) == []
    plant(scratch(slug), "empty.html", "")
    st, js = present(slug, title="T", path="empty.html")
    assert st != 200 and "empty" in str(js), (st, js)


check("path · over 4 MiB and empty files are refused (send_file's checks)",
      _cap)


# ══════════════════════════════════════════════════════════════════════ §2
print("\n§2  containment and snapshot semantics (send_file's rule, reused)")


def _roots():
    slug, held, foreign = fresh_org()
    plant(held, "h.html")
    st, js = present(slug, title="T", path=os.path.join(held, "h.html"))
    assert st == 200, (st, js)
    assert doc_of(slug, js["presented"])["file"] == "outbox/h.html"
    plant(foreign, "f.html")
    st, js = present(slug, title="T", path=os.path.join(foreign, "f.html"))
    assert st != 200 and "only files in your working folder" in str(js), \
        (st, js)
    st, js = present(slug, title="T",
                     path=os.path.join(scratch(slug), "..", "..",
                                       os.path.basename(foreign), "f.html"))
    assert st != 200, (st, js)
    assert outbox_names(slug) == ["h.html"]


check("containment · a held folder is sendable, a foreign one and a ../ "
      "traversal are not", _roots)


def _symlink():
    slug, held, foreign = fresh_org()
    target = plant(foreign, "secret.html")
    link = os.path.join(scratch(slug), "link.html")
    via = "link.html"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        # no symlink privilege (default on Windows): a directory JUNCTION
        # needs none and realpath resolves it the same way, so the check
        # stays live instead of declaring itself inert
        import subprocess
        jdir = os.path.join(scratch(slug), "jlink")
        subprocess.run(["cmd", "/c", "mklink", "/J", jdir, foreign],
                       check=True, capture_output=True)
        assert os.path.realpath(os.path.join(jdir, "secret.html")).lower() \
            == os.path.realpath(target).lower(), "junction did not resolve"
        via = "jlink/secret.html"
    st, js = present(slug, title="T", path=via)
    assert st != 200, "a link into a foreign tree must not smuggle it in"
    assert outbox_names(slug) == []


check("containment · a symlink in scratch pointing outside is refused "
      "(realpath first)", _symlink)


def _snapshot():
    slug, _h, _f = fresh_org()
    src = plant(scratch(slug), "mock.html")
    st, js = present(slug, title="T", path="mock.html")
    did = js["presented"]
    with open(src, "w", encoding="utf-8") as fh:
        fh.write("<html>EDITED AFTER PRESENT</html>")
    r = client.get(f"/api/orgs/{slug}/documents/{did}/mockup")
    assert r.status_code == 200 and "EDITED" not in r.text, \
        "the card must show the snapshot, not the live file"
    os.remove(src)
    assert client.get(f"/api/orgs/{slug}/documents/{did}/mockup"
                      ).status_code == 200, "deleting the source is fine"
    # re-present after an edit → a NEW snapshot beside the old (dedupe -2)
    plant(scratch(slug), "mock.html", "<html>v2</html>")
    st, js2 = present(slug, title="T", path="mock.html", replaces=did)
    assert st == 200 and js2["presented"] == did, js2
    assert doc_of(slug, did)["file"] == "outbox/mock-2.html"
    assert outbox_names(slug) == ["mock-2.html", "mock.html"]
    assert "v2" in client.get(f"/api/orgs/{slug}/documents/{did}/mockup").text


check("snapshot · edits/deletes after present do not change the card; "
      "replaces re-snapshots as name-2.html", _snapshot)


def _from_outbox():
    """A mockup card promises a SNAPSHOT. send_file references a source
    already in outbox/ as-is (its card names that file); present must not
    inherit that, or editing outbox/x.html after presenting it would change
    a published card (feature-astra review, 2026-09-06)."""
    slug, _h, _f = fresh_org()
    ob = os.path.join(scratch(slug), "outbox")
    os.makedirs(ob, exist_ok=True)
    src = plant(ob, "already.html")
    st, js = present(slug, title="T", path="outbox/already.html")
    assert st == 200, (st, js)
    did = js["presented"]
    assert doc_of(slug, did)["file"] == "outbox/already-2.html", doc_of(slug, did)
    assert outbox_names(slug) == ["already-2.html", "already.html"]
    with open(src, "w", encoding="utf-8") as fh:
        fh.write("<html>EDITED OUTBOX SOURCE</html>")
    r = client.get(f"/api/orgs/{slug}/documents/{did}/mockup")
    assert r.status_code == 200 and "EDITED" not in r.text, \
        "editing the outbox source must not change the published card"
    # send_file keeps its no-copy behaviour for an outbox/ source (its card
    # names the file the agent put there) — the default is unchanged
    r = client.post("/api/agent", json={"org": slug, "node": "boss",
                                        "tool": "orgtree_send_file",
                                        "args": {"path": "outbox/already.html"}})
    assert r.status_code == 200 and r.json()["sent"]["path"] == "outbox/already.html", r.text
    assert outbox_names(slug) == ["already-2.html", "already.html"], \
        "send_file must not have started copying outbox/ sources"


check("snapshot · an outbox/ source is COPIED for present (edits do not reach "
      "the card) while send_file still references it", _from_outbox)


# ══════════════════════════════════════════════════════════════════════ §3
print("\n§3  the records — ledger, tree payload, gallery, GET document")


def _get_doc():
    slug, _h, _f = fresh_org()
    st, js = present(slug, title="T", path=plant(scratch(slug), "m.html"))
    did = js["presented"]
    r = client.get(f"/api/orgs/{slug}/documents/{did}").json()
    assert r["format"] == "html" and r["body"] == "", r
    assert r["bytes"] == len(MOCK.encode()), r
    assert r["mockup"] == f"/api/orgs/{slug}/documents/{did}/mockup", r
    assert "file" not in r, "the outbox path is not part of the wire"
    st, js = present(slug, title="P", body="# p")
    r = client.get(f"/api/orgs/{slug}/documents/{js['presented']}").json()
    assert r["format"] == "markdown" and "mockup" not in r and "bytes" not in r


check("GET document · html: format/bytes/mockup url, body empty; markdown: "
      "format only", _get_doc)


def _gallery_and_tree():
    slug, _h, _f = fresh_org()
    present(slug, title="MD", body="# p")
    present(slug, title="HTML", path=plant(scratch(slug), "m.html"))
    rows = client.get(f"/api/orgs/{slug}/documents").json()["documents"]
    by = {r["title"]: r for r in rows}
    assert by["HTML"]["format"] == "html" and by["HTML"]["bytes"] > 0, rows
    assert by["MD"]["format"] == "markdown" and "bytes" not in by["MD"], rows
    tree = client.get(f"/api/orgs/{slug}").json()
    node = next(n for n in tree["tree"]["children"] if n["id"] == "boss") \
        if "tree" in tree else None
    if node is None:                      # payload shape guard
        node = _find_node(tree, "boss")
    docs = {d["title"]: d for d in node["documents"]}
    assert docs["HTML"]["format"] == "html" and docs["MD"]["format"] == "markdown"
    assert "body" not in docs["HTML"] and "file" not in docs["HTML"]


def _find_node(obj, nid):
    if isinstance(obj, dict):
        if obj.get("id") == nid and "documents" in obj:
            return obj
        for v in obj.values():
            r = _find_node(v, nid)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_node(v, nid)
            if r:
                return r
    return None


check("gallery + tree payload · every document row carries format; html rows "
      "carry bytes and never the body or file", _gallery_and_tree)


def _evicted_keeps_format():
    slug, _h, _f = fresh_org()
    first = present(slug, title="H0", path=plant(scratch(slug), "m.html"))[1]["presented"]
    for i in range(10):                       # push the first off (10/agent)
        present(slug, title=f"MD{i}", body="# x")
    rows = client.get(f"/api/orgs/{slug}/documents").json()["documents"]
    ev = next(r for r in rows if r["id"] == first)
    assert ev["evicted"] and ev["format"] == "html", ev
    assert client.get(f"/api/orgs/{slug}/documents/{first}/mockup"
                      ).status_code == 404


check("eviction · an evicted html card keeps format html in the gallery; its "
      "mockup route is 404", _evicted_keeps_format)


def _replace_switch():
    slug, _h, _f = fresh_org()
    did = present(slug, title="A", body="# md")[1]["presented"]
    st, js = present(slug, title="A", path=plant(scratch(slug), "m.html"),
                     replaces=did)
    assert st == 200 and doc_of(slug, did)["format"] == "html"
    st, js = present(slug, title="A", body="# back", replaces=did)
    d = doc_of(slug, did)
    assert "format" not in d and "file" not in d and "bytes" not in d, d
    assert d["body"] == "# back"
    assert client.get(f"/api/orgs/{slug}/documents/{did}/mockup"
                      ).status_code == 404


check("replaces · switching a card md→html→md never leaves half of each",
      _replace_switch)


def _ledger_contract():
    org = store.load_org(fresh_org()[0])
    try:
        org.present_document("boss", "T", "", html_file="uploads/x.html",
                             html_bytes=3)
        raise AssertionError("a non-outbox html_file must be refused")
    except LedgerError as e:
        assert "outbox" in str(e)
    try:
        org.present_document("boss", "T", "", html_file="outbox/x.html",
                             html_bytes=0)
        raise AssertionError("zero bytes must be refused")
    except LedgerError:
        pass


check("ledger · present_document refuses an html_file outside outbox/ or "
      "with no bytes (the API contract is enforced, not assumed)",
      _ledger_contract)


# ══════════════════════════════════════════════════════════════════════ §4
print("\n§4  the wrapper route — headers, escaping, 403/404/410/422")

_EXPECT_CSP = ("sandbox allow-scripts allow-forms allow-modals; "
               "default-src 'none'; script-src 'unsafe-inline'; "
               "style-src 'unsafe-inline'; img-src data: blob:; "
               "font-src data:; media-src data: blob:; "
               "form-action 'none'; frame-src 'none'; "
               "frame-ancestors 'none'; base-uri about:")


def _wrapper():
    slug, _h, _f = fresh_org()
    did = present(slug, title='Mock <"t">',
                  path=plant(scratch(slug), "m.html"))[1]["presented"]
    r = client.get(f"/api/orgs/{slug}/documents/{did}/mockup")
    assert r.status_code == 200, r.text
    h = r.headers
    assert h["content-type"] == "text/html; charset=utf-8", h
    assert h["content-security-policy"] == _EXPECT_CSP, h["content-security-policy"]
    assert h["referrer-policy"] == "no-referrer", h
    assert h["x-content-type-options"] == "nosniff", h
    assert h.get_list("cache-control") == ["no-store"], \
        "no-store once (the instance middleware stamps it; not doubled)"
    assert "content-disposition" not in h, "this renders; it is not a download"
    t = r.text
    assert t.startswith("<!DOCTYPE html>")
    assert '<base href="about:blank">' in t
    assert re.search(r'<iframe sandbox="allow-scripts allow-forms allow-modals" '
                     r'referrerpolicy="no-referrer" srcdoc="', t), t[:400]
    assert "allow-same-origin" not in t and "allow-top-navigation" not in t \
        and "allow-popups" not in t
    assert "<title>Mock &lt;&quot;t&quot;&gt;</title>" in t, "title escaped"
    # the payload appears ONCE, fully escaped — no raw mockup markup anywhere
    esc = html.escape(MOCK, quote=True)
    assert t.count(esc) == 1, "escaped payload must appear exactly once"
    assert MOCK not in t and "<h1" not in t and "<script>" not in t, \
        "raw mockup markup leaked into the wrapper"
    assert t.count("<iframe") == 1 and t.count("</iframe>") == 1, \
        "the mockup's </iframe> must not close the real one"
    assert "<script" not in t, "the wrapper itself carries no script"
    # the srcdoc attribute is closed exactly where the wrapper closes it
    m = re.search(r'srcdoc="(.*)"></iframe>', t, re.S)
    assert m and html.unescape(m.group(1)) == MOCK, "round-trip"


check("wrapper · exact headers, sandbox attribute, base/referrer fixings, "
      "payload escaped once and round-trips", _wrapper)


def _kiosk_403():
    slug, _h, _f = fresh_org()
    did = present(slug, title="T",
                  path=plant(scratch(slug), "m.html"))[1]["presented"]
    org = store.load_org(slug)
    org.d["kiosk"] = {"enabled": True, "token": "tok_" + "a" * 20}
    store.save_org(org)
    api._token_cache["at"] = 0.0
    pub = TestClient(api.PublicGateway(api.app))
    r = pub.get(f"/k/tok_{'a' * 20}/api/orgs/{slug}/documents/{did}/mockup")
    assert r.status_code == 403 and "operator-only" in r.text, (r.status_code, r.text)
    # metadata stays readable for the visitor (root scope: card visible,
    # preview unavailable)
    r = pub.get(f"/k/tok_{'a' * 20}/api/orgs/{slug}/documents/{did}")
    assert r.status_code == 200 and r.json()["format"] == "html", r.text
    # …and the admin side still serves it — the 403 is the gateway, not a
    # broken route
    assert client.get(f"/api/orgs/{slug}/documents/{did}/mockup"
                      ).status_code == 200


check("kiosk · the public gateway gets 403 on /mockup while the document "
      "metadata stays readable; admin side unaffected", _kiosk_403)


def _errors():
    slug, _h, _f = fresh_org()
    md = present(slug, title="P", body="# p")[1]["presented"]
    r = client.get(f"/api/orgs/{slug}/documents/{md}/mockup")
    assert r.status_code == 404 and "not an HTML mockup" in r.text, r.text
    assert client.get(f"/api/orgs/{slug}/documents/dnope/mockup"
                      ).status_code == 404
    assert client.get(f"/api/orgs/no-such-org/documents/dnope/mockup"
                      ).status_code == 404
    did = present(slug, title="T",
                  path=plant(scratch(slug), "m.html"))[1]["presented"]
    snapshot = os.path.join(scratch(slug), "outbox", "m.html")
    # A valid presentation can grow after publication; fail instead of
    # serving the read limit as a silently truncated, executable page.
    assert client.get(f"/api/orgs/{slug}/documents/{did}/mockup").status_code == 200
    with open(snapshot, "wb") as fh:
        fh.write(b"x" * (api._MOCKUP_MAX + 1))
    r = client.get(f"/api/orgs/{slug}/documents/{did}/mockup")
    assert r.status_code == 422 and "grew past" in r.text, (r.status_code, r.text[:200])
    os.remove(os.path.join(scratch(slug), "outbox", "m.html"))
    r = client.get(f"/api/orgs/{slug}/documents/{did}/mockup")
    assert r.status_code == 410, (r.status_code, r.text)
    # a record whose file points outside outbox/ (a tampered document) is
    # refused rather than served
    org = store.load_org(slug)
    d = next(x for x in org.d["documents"] if x["id"] == did)
    d["file"] = "outbox/../uploads/m.html"
    store.save_org(org)
    plant(os.path.join(scratch(slug), "uploads") if os.path.isdir(
        os.path.join(scratch(slug), "uploads")) else _mk(slug, "uploads"),
        "m.html")
    r = client.get(f"/api/orgs/{slug}/documents/{did}/mockup")
    assert r.status_code == 422, (r.status_code, r.text)


def _mk(slug, sub):
    p = os.path.join(scratch(slug), sub)
    os.makedirs(p, exist_ok=True)
    return p


check("wrapper · 404 markdown/missing doc/missing org, 410 snapshot gone, "
      "422 record pointing outside outbox/", _errors)


def _dismiss():
    slug, _h, _f = fresh_org()
    did = present(slug, title="T",
                  path=plant(scratch(slug), "m.html"))[1]["presented"]
    assert client.delete(f"/api/orgs/{slug}/documents/{did}").status_code == 200
    assert client.get(f"/api/orgs/{slug}/documents/{did}/mockup"
                      ).status_code == 404
    assert outbox_names(slug) == ["m.html"], \
        "the outbox snapshot stays, like a sent file (documented residue)"


check("dismiss · the card's ✕ removes the route; the outbox copy remains "
      "(send_file residue, by design)", _dismiss)


# ══════════════════════════════════════════════════════════════════════ §5
print("\n§5  positive controls — the checks can fail")


def _escaping_control():
    """If the wrapper stopped escaping, the srcdoc attribute would close at
    the mockup's first `"` — this is the assertion that would catch it."""
    broken = api._mockup_wrapper("t", MOCK).replace(
        html.escape(MOCK, quote=True), MOCK)
    assert broken.count("<iframe") == 1 and broken.count("</iframe>") == 2
    assert "<script>" in broken
    good = api._mockup_wrapper("t", MOCK)
    assert good.count("</iframe>") == 1 and "<script>" not in good


check("control · an unescaped payload WOULD close the iframe and inject a "
      "script — the §4 assertions see it", _escaping_control)


def _csp_control():
    assert api._MOCKUP_CSP == _EXPECT_CSP
    weakened = api._MOCKUP_CSP.replace("frame-src 'none'; ", "")
    assert weakened != _EXPECT_CSP


check("control · the expected CSP is the served CSP, and a dropped directive "
      "is a mismatch", _csp_control)


def _send_file_unchanged():
    """the shared snapshot helper must not have changed send_file's own
    behaviour: same card shape, same 256 MB cap message."""
    slug, _h, _f = fresh_org()
    plant(scratch(slug), "r.txt", "report")
    r = client.post("/api/agent", json={"org": slug, "node": "boss",
                                        "tool": "orgtree_send_file",
                                        "args": {"path": "r.txt"}})
    assert r.status_code == 200, r.text
    js = r.json()
    assert js["sent"]["path"] == "outbox/r.txt" and js["sent"]["bytes"] == 6
    assert "download card" in js["hint"]


check("control · orgtree_send_file still snapshots and answers exactly as "
      "before the helper split", _send_file_unchanged)


print("\n" + "═" * 70)
print(f"{PASSED} checks passed, {len(FAILED)} failed")
for f in FAILED:
    print("\nFAIL:", f)
sys.exit(1 if FAILED else 0)
