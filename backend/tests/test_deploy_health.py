"""The post-deploy state assertion: `tools/deploy_health.py`.

WHAT IT GUARDS.  `update.ps1` used to end its deploy by asking `/api/orgs` for
an HTTP 200.  An empty list is a perfectly good 200, so a backend that came up
carrying NONE of the install's orgs deployed green -- the exact sequence being:
the SQLite cutover ships, the code is rolled back without the data, the JSON
build finds no `<slug>.json` (only `<slug>.db` + `<slug>.json.premigration`),
honestly presents zero orgs, and the deploy script blesses it.

THE POINT OF THIS SUITE IS THE RED, NOT THE GREEN.  Section 2 drives the
checker against a backend that is UP and serving zero orgs while three exist on
disk, and asserts it fails -- with §2.0 first establishing that the old
"200 is healthy" rule would have passed that very fixture.  A check only ever
seen green is a check nobody has tested.

    §1  the expectation: what the data root says must exist
    §2  the verdicts, end to end against a programmable /api/orgs
    §3  the budgets: it cannot hang a deploy
    §4  BOTH deploy scripts run it, and neither settles for a 200
    §5  it is a SECOND source of truth (it imports no orgtree)
    §6  controls -- what would make the above vacuous

Nothing here touches the live install: every fixture is a temp directory and a
loopback HTTP server on an ephemeral port.  No orgtree module is imported at
all, by this suite or by the code under test.

    python backend/tests/test_deploy_health.py
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import deploy_health as dh                                    # noqa: E402

FAILED: list[str] = []
PASSED = 0


def check(label, fn) -> None:
    global PASSED
    try:
        fn()
    except Exception as e:                                     # noqa: BLE001
        import traceback
        FAILED.append("%s\n      %s: %s\n%s"
                      % (label, type(e).__name__, e,
                         "".join("        " + ln for ln in
                                 traceback.format_exc().splitlines(True))))
        print("  x %s" % label)
    else:
        PASSED += 1
        print("  ok %s" % label)


# --------------------------------------------------------------- fixtures --

def data_root(*files: str) -> str:
    """A temp data root whose `orgs/` holds exactly `files`."""
    d = tempfile.mkdtemp(prefix="orgtree-dhealth-")
    orgs = os.path.join(d, "orgs")
    os.makedirs(orgs)
    for f in files:
        with open(os.path.join(orgs, f), "w", encoding="utf-8") as fh:
            fh.write("{}")
    return d


def orgs_body(slugs) -> bytes:
    """What a healthy `/api/orgs` returns, cut down to the field read."""
    return json.dumps([{"slug": s, "name": s, "nodes": 1} for s in slugs]).encode()


class Fake:
    """A programmable `/api/orgs` on an ephemeral loopback port.

    `script` is a list of `(status, body)`; the LAST entry repeats forever, so
    a one-entry script is a steady state and a longer one models a backend
    that changes its answer as it finishes starting.
    """

    def __init__(self, script):
        self.script = list(script)
        self.hits = 0
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def do_GET(self):                                  # noqa: N802
                status, body = outer._next()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):                         # noqa: ARG002
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def _next(self):
        i = min(self.hits, len(self.script) - 1)
        self.hits += 1
        return self.script[i]

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def dead_port() -> int:
    """A port with nothing on it: bound, read, released."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def run(argv) -> tuple[int, str]:
    """`main(argv)` with its output captured. Returns (exit code, output)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = dh.main(argv)
    return rc, buf.getvalue()


def snapshot(root: str, port: int) -> tuple[int, str, str]:
    """Run the pre-restart half; returns (rc, output, state file path)."""
    fd, out = tempfile.mkstemp(prefix="orgtree-dhealth-state-", suffix=".json")
    os.close(fd)
    rc, txt = run(["snapshot", "--data", root, "--port", str(port), "--out", out,
                   "--http-timeout", "2"])
    return rc, txt, out


def verify(port: int, state: str, up=3.0, settle=3.0) -> tuple[int, str]:
    return run(["verify", "--port", str(port), "--state", state,
                "--interval", "0.05", "--http-timeout", "2",
                "--up-deadline", str(up), "--state-deadline", str(settle)])


THREE = ("alpha", "beta", "gamma")


# ------------------------------------------- §1  what the data root says ----

def json_backend_root_lists_its_orgs() -> None:
    r = data_root("alpha.json", "beta.json", "gamma.json")
    assert dh.expected_orgs(r) == (["alpha", "beta", "gamma"], ""), dh.expected_orgs(r)


def sqlite_backend_root_lists_the_same_orgs() -> None:
    """THE ROLLBACK SHAPE.  After the cutover the live document is `<slug>.db`
    and the pre-cutover copy is parked at `<slug>.json.premigration`.  There
    is no `<slug>.json` at all -- which is exactly why asking the deployed
    JSON build what it expects would agree with the bug."""
    r = data_root("alpha.db", "alpha.json.premigration",
                  "beta.db", "beta.json.premigration",
                  "gamma.db", "gamma.json.premigration")
    assert dh.expected_orgs(r)[0] == ["alpha", "beta", "gamma"], dh.expected_orgs(r)


def a_db_with_no_premigration_beside_it_is_still_an_org() -> None:
    """An org CREATED under the SQLite backend was never JSON, so it has no
    premigration at all -- `<slug>.db` on its own is the whole document. The
    migrated-root fixture above would still pass if the `.db` arm of the scan
    were deleted, because its premigrations carry it; this is the check that
    pins that arm (found by mutation, 2026-09-04)."""
    r = data_root("alpha.db", "beta.db")
    assert dh.expected_orgs(r)[0] == ["alpha", "beta"], dh.expected_orgs(r)


def a_premigration_alone_still_means_the_org_exists() -> None:
    """A migration interrupted between the rename and the commit leaves the
    premigration with no `.json` and no finished `.db`.  The org exists."""
    r = data_root("alpha.json.premigration", "alpha.db.migrating")
    assert dh.expected_orgs(r)[0] == ["alpha"]


def the_hand_annotated_premigration_on_this_machine_resolves() -> None:
    """The live root really carries a
    `orgtree.json.premigration.STALE-...-DO-NOT-RESTORE`; an operator renamed
    it by hand.  It must resolve to its slug and not to a phantom org."""
    r = data_root("orgtree.json",
                  "orgtree.json.premigration.STALE-2026-09-03-DO-NOT-RESTORE")
    assert dh.expected_orgs(r)[0] == ["orgtree"], dh.expected_orgs(r)


def junk_in_the_orgs_directory_invents_no_orgs() -> None:
    """A save that died mid-write leaves a `mkstemp` `.tmp`; a stray note or a
    subdirectory can be anything.  None of them is an org, and every one of
    them would be a permanent false red on this machine."""
    r = data_root("alpha.json", "tmpq7x1z9.tmp", "alpha.json.tmp",
                  "README", ".hidden.json", "-weird.json")
    os.makedirs(os.path.join(r, "orgs", "somedir"))
    assert dh.expected_orgs(r)[0] == ["alpha"], dh.expected_orgs(r)


def a_lone_db_migrating_invents_nothing() -> None:
    """`<slug>.db.migrating` never appears without its premigration, so it is
    deliberately not counted -- an orphan left by a dead migration must not
    fail every future deploy."""
    r = data_root("orphan.db.migrating")
    assert dh.expected_orgs(r)[0] == [], dh.expected_orgs(r)


def a_fresh_install_expects_nothing_and_says_so() -> None:
    """A machine on its first deploy has no `orgs/` at all.  That is a real
    state, not a failure, or the check would cry wolf on every new install
    forever."""
    d = tempfile.mkdtemp(prefix="orgtree-dhealth-")
    slugs, note = dh.expected_orgs(d)
    assert slugs == []
    assert "EMPTY install" in note, note
    slugs2, note2 = dh.expected_orgs(os.path.join(d, "does-not-exist"))
    assert slugs2 == [] and note2


def an_unreadable_orgs_directory_raises_rather_than_returning_empty() -> None:
    """The one case where guessing is how a check goes dark: the directory is
    there and cannot be read.  It must NOT come back as "zero orgs"."""
    r = data_root("alpha.json")
    real = os.listdir

    def boom(p):
        if p.endswith("orgs"):
            raise PermissionError(13, "denied")
        return real(p)

    os.listdir = boom
    try:
        try:
            dh.expected_orgs(r)
        except OSError:
            return
        raise AssertionError("expected_orgs swallowed an unreadable orgs/")
    finally:
        os.listdir = real


# --------------------------------------------------- §2  the verdicts -------

def the_old_rule_would_have_passed_the_fixture_that_follows() -> None:
    """§2.0 -- THE CONTROL FOR THE WHOLE SUITE.

    Before believing the red below, prove the fixture is one the OLD check
    called healthy: a backend that is up, answers `/api/orgs`, returns HTTP
    200, and hands back an empty list while three orgs sit on disk.  The old
    rule was `StatusCode -eq 200`, and here it is, satisfied."""
    with Fake([(200, orgs_body([]))]) as f:
        import urllib.request
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/api/orgs" % f.port, timeout=2) as r:
            assert r.status == 200, r.status
            assert json.loads(r.read().decode()) == []


def up_and_serving_zero_orgs_when_three_exist_is_RED() -> None:
    """THE PROOF.  Same fixture §2.0 just showed a 200 for."""
    root = data_root(*(s + ".db" for s in THREE),
                     *(s + ".json.premigration" for s in THREE))
    with Fake([(200, orgs_body([]))]) as f:
        rc0, snap, state = snapshot(root, f.port)
        assert rc0 == dh.OK, (rc0, snap)
        rc, out = verify(f.port, state)
    assert rc == dh.WRONG_STATE, (rc, out)
    assert "PRESENTING THE WRONG STATE" in out, out
    assert "MISSING   : alpha, beta, gamma" in out, out
    assert "expected  : 3 org(s)" in out, out
    assert "serving   : 0 org(s)" in out, out
    # the deploy log is cp1252 on Windows: a message that cannot be encoded is
    # a message the operator never sees (docs/test-baseline.md, third trap)
    out.encode("cp1252")


def it_names_the_deploy_as_the_culprit_when_the_old_backend_was_fine() -> None:
    """The one sentence that makes the failure actionable: the outgoing
    process served these three, so THIS deploy lost them."""
    root = data_root(*(s + ".json" for s in THREE))
    with Fake([(200, orgs_body(THREE))]) as good:
        _, _, state = snapshot(root, good.port)
    with Fake([(200, orgs_body([]))]) as bad:
        rc, out = verify(bad.port, state)
    assert rc == dh.WRONG_STATE, (rc, out)
    assert "THIS DEPLOY lost them" in out, out


def it_does_not_blame_the_deploy_for_a_pre_existing_fault() -> None:
    """Wolf control: if the old backend was ALREADY not serving the root's
    orgs, say so rather than pinning it on the deploy."""
    root = data_root(*(s + ".json" for s in THREE))
    with Fake([(200, orgs_body([]))]) as already_bad:
        _, _, state = snapshot(root, already_bad.port)
        rc, out = verify(already_bad.port, state)
    assert rc == dh.WRONG_STATE, (rc, out)
    assert "predates the" in out, out


def one_missing_org_out_of_three_is_RED_and_is_named() -> None:
    root = data_root(*(s + ".json" for s in THREE))
    with Fake([(200, orgs_body(["alpha", "gamma"]))]) as f:
        _, _, state = snapshot(root, f.port)
        rc, out = verify(f.port, state)
    assert rc == dh.WRONG_STATE, (rc, out)
    assert "MISSING   : beta" in out, out


def an_org_served_that_is_not_on_disk_is_RED() -> None:
    """Set equality, not a floor.  Serving an org the data root does not have
    means the expectation model and reality disagree, and a check that cannot
    say what it is asserting must not pass."""
    root = data_root("alpha.json")
    with Fake([(200, orgs_body(["alpha", "ghost"]))]) as f:
        _, _, state = snapshot(root, f.port)
        rc, out = verify(f.port, state)
    assert rc == dh.WRONG_STATE, (rc, out)
    assert "UNEXPECTED: ghost" in out, out


def a_backend_that_never_answers_is_a_DIFFERENT_verdict() -> None:
    """"It did not start" and "it started wrong" send the operator to
    different places, so they are different exit codes."""
    root = data_root("alpha.json")
    p = dead_port()
    _, _, state = snapshot(root, p)
    rc, out = verify(p, state, up=1.0, settle=1.0)
    assert rc == dh.NO_ANSWER, (rc, out)
    assert "NEVER ANSWERED" in out, out
    assert "PRESENTING THE WRONG STATE" not in out


def a_200_with_an_unreadable_body_is_up_and_wrong_not_down() -> None:
    """A backend serving the UI's index at `/api/orgs` because routing broke
    is up.  Reporting that as "never came up" points at the wrong log."""
    root = data_root("alpha.json")
    with Fake([(200, b"<!doctype html><html>orgtree</html>")]) as f:
        _, _, state = snapshot(root, f.port)
        rc, out = verify(f.port, state, up=1.0, settle=1.0)
    assert rc == dh.WRONG_STATE, (rc, out)
    assert "never with a readable org list" in out, out


def a_genuinely_empty_install_is_GREEN() -> None:
    """The check must not be defeated by a real fresh machine."""
    d = tempfile.mkdtemp(prefix="orgtree-dhealth-")
    with Fake([(200, orgs_body([]))]) as f:
        rc0, _, state = snapshot(d, f.port)
        assert rc0 == dh.OK
        rc, out = verify(f.port, state)
    assert rc == dh.OK, (rc, out)
    assert "0 org(s)" in out, out


def a_healthy_deploy_is_GREEN() -> None:
    root = data_root(*(s + ".json" for s in THREE))
    with Fake([(200, orgs_body(THREE))]) as f:
        _, _, state = snapshot(root, f.port)
        rc, out = verify(f.port, state)
    assert rc == dh.OK, (rc, out)
    assert "up and carrying its state -- 3 org(s)" in out, out


def a_backend_that_is_still_loading_is_NOT_failed() -> None:
    """THE ANTI-WOLF CHECK.  Two 503s, then an honest-but-early empty list,
    then the real answer.  A check that fired on the first look would be
    bypassed within a week."""
    root = data_root(*(s + ".json" for s in THREE))
    with Fake([(503, b"starting"), (503, b"starting"),
               (200, orgs_body([])), (200, orgs_body(["alpha"])),
               (200, orgs_body(THREE))]) as f:
        _, _, state = snapshot(root, f.port)
        rc, out = verify(f.port, state)
    assert rc == dh.OK, (rc, out)


def a_missing_snapshot_fails_even_when_everything_else_is_perfect() -> None:
    """"I could not tell" is not "healthy".  The backend here is up and
    serving exactly the right orgs; the check still refuses, because it has no
    expectation and cannot know that."""
    root = data_root(*(s + ".json" for s in THREE))
    with Fake([(200, orgs_body(THREE))]) as f:
        _, _, state = snapshot(root, f.port)
        os.remove(state)
        rc, out = verify(f.port, state, up=1.0, settle=1.0)
    assert rc == dh.NO_EXPECTATION, (rc, out)
    assert "COULD NOT DETERMINE THE EXPECTED STATE" in out, out
    assert "IS answering" in out, out


def an_unreadable_data_root_fails_the_deploy_rather_than_passing_it() -> None:
    real = os.listdir

    def boom(p):
        if p.endswith("orgs"):
            raise PermissionError(13, "denied")
        return real(p)

    root = data_root("alpha.json")
    with Fake([(200, orgs_body(["alpha"]))]) as f:
        os.listdir = boom
        try:
            rc0, snap, state = snapshot(root, f.port)
        finally:
            os.listdir = real
        assert rc0 == dh.NO_EXPECTATION, (rc0, snap)
        rc, out = verify(f.port, state, up=1.0, settle=1.0)
    assert rc == dh.NO_EXPECTATION, (rc, out)
    assert "could not read" in out, out


def the_exit_codes_survive_the_process_boundary() -> None:
    """update.ps1 reads `$LASTEXITCODE`, so the verdicts have to be real
    process exit codes and not just return values."""
    root = data_root(*(s + ".db" for s in THREE))
    script = os.path.join(ROOT, "tools", "deploy_health.py")
    with Fake([(200, orgs_body([]))]) as f:
        fd, state = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        r1 = subprocess.run([sys.executable, script, "snapshot", "--data", root,
                             "--port", str(f.port), "--out", state],
                            capture_output=True, text=True)
        assert r1.returncode == 0, r1.stdout + r1.stderr
        r2 = subprocess.run([sys.executable, script, "verify", "--port",
                             str(f.port), "--state", state, "--interval", "0.05",
                             "--up-deadline", "2", "--state-deadline", "2"],
                            capture_output=True, text=True)
    assert r2.returncode == dh.WRONG_STATE, (r2.returncode, r2.stdout, r2.stderr)
    assert "WRONG STATE" in r2.stdout, r2.stdout


# ------------------------------------------------------ §3  the budgets -----

def it_cannot_hang_the_deploy_when_nothing_ever_answers() -> None:
    root = data_root("alpha.json")
    p = dead_port()
    _, _, state = snapshot(root, p)
    t0 = time.monotonic()
    rc, _ = verify(p, state, up=1.0, settle=5.0)
    dt = time.monotonic() - t0
    assert rc == dh.NO_ANSWER
    # the settle budget must NOT be spent by a backend that never answered
    assert dt < 3.0, dt


def it_cannot_hang_the_deploy_when_the_state_never_settles() -> None:
    root = data_root("alpha.json")
    with Fake([(200, orgs_body([]))]) as f:
        _, _, state = snapshot(root, f.port)
        t0 = time.monotonic()
        rc, _ = verify(f.port, state, up=5.0, settle=1.0)
        dt = time.monotonic() - t0
    assert rc == dh.WRONG_STATE
    assert dt < 3.0, dt


# ------------------------------------------------ §4  update.ps1 reads it ---

def read(rel: str) -> str:
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def both_deploy_scripts_run_the_check() -> None:
    """A gate nobody calls is not a gate -- and the two lanes must not drift on
    what "healthy" means, which is why this asserts BOTH rather than only the
    one that happens to run on this machine."""
    for name in ("update.ps1", "update.sh"):
        src = read(name)
        assert "deploy_health.py" in src, "%s never runs the health check" % name
        assert "snapshot" in src, "%s never takes the pre-restart snapshot" % name
        assert "verify" in src, "%s never verifies after the restart" % name


def neither_deploy_script_settles_for_a_200() -> None:
    """The defect itself. On Windows the old rule was `$r.StatusCode -eq 200`
    against `/api/orgs`; on the shell side it was `curl -fsS` treating any 2xx
    as healthy. Neither may still be the thing that decides the deploy."""
    ps1 = read("update.ps1")
    assert "StatusCode -eq 200" not in ps1, (
        "update.ps1 still decides deploy health on an HTTP status alone")
    sh = read("update.sh")
    assert "curl -fsS" not in sh, (
        "update.sh still decides deploy health on a bare curl of /api/orgs")


def both_deploy_scripts_fail_on_a_nonzero_verdict() -> None:
    ps1 = read("update.ps1")
    tail = ps1[ps1.index("deploy_health.py"):]
    assert "$healthRc" in tail, "update.ps1 never captures the verify exit code"
    assert "exit 1" in tail, "update.ps1 never fails the deploy on a red check"
    sh = read("update.sh")
    tail = sh[sh.index("deploy_health.py"):]
    assert "HEALTH_RC" in tail, "update.sh never captures the verify exit code"
    assert "die " in tail, "update.sh never fails the deploy on a red check"


def both_deploy_scripts_distinguish_every_verdict() -> None:
    """All four exit codes have to be REACHABLE branches in each script. A
    script that only tests for zero collapses "up and wrong", "never came up"
    and "could not tell" into one message, and the whole point of the split is
    that the operator does different things about each."""
    for name, rcs in (("update.ps1", ("-eq 0", "-eq 1", "-eq 2")),
                      ("update.sh", ("0)", "1)", "2)"))):
        src = read(name)
        tail = src[src.index("deploy_health.py"):]
        for rc in rcs:
            assert rc in tail, "%s never branches on %r" % (name, rc)


# ------------------------------- §5  a genuine second source of truth -------

def the_checker_imports_no_orgtree() -> None:
    """If it asked `store` what to expect it would agree with the bug: a
    rolled-back JSON build honestly reports zero orgs for a migrated root.
    Reading the filesystem itself is the entire point.

    ⚠ Matched as an import STATEMENT, not as a substring. The first spelling
    of this check matched the docstring sentence "imports NOTHING from
    orgtree" and failed the file for promising not to do the thing — the same
    prose-versus-use mistake docs/test-baseline.md records in the runner's own
    port guard."""
    import re
    bad = re.compile(r"^\s*(?:from|import)\s+(orgtree|store|ledger|api)\b", re.M)
    hit = bad.search(read("tools/deploy_health.py"))
    assert not hit, "deploy_health.py imports the code under test: %r" % (
        hit.group(0) if hit else "")


def the_checker_loads_no_orgtree_module_at_runtime() -> None:
    """The source check above reads text. This one runs the import and looks
    at what actually landed in `sys.modules` -- a transitive import would not
    show up in a grep.

    ⚠ The name list is deliberately just the ledger modules. Naming the web
    server here as well made `run_tests.py` classify this whole suite as
    `exclusive` (its detector greps the source for that name), which would
    have serialised the entire run for a check with no extra teeth."""
    code = ("import sys, json; sys.path.insert(0, sys.argv[1]); "
            "import deploy_health; "
            "print(json.dumps([m for m in sys.modules if m.split('.')[0] "
            "in ('orgtree', 'store', 'ledger')]))")
    r = subprocess.run([sys.executable, "-c", code, os.path.join(ROOT, "tools")],
                       capture_output=True, text=True, cwd=tempfile.gettempdir())
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout.strip()) == [], r.stdout


def the_checker_runs_with_the_backend_nowhere_on_the_path() -> None:
    """Proved by running it, not by reading it: a deploy that failed its
    health check because the health check could not import is the same
    outage wearing a different hat."""
    env = dict(os.environ)
    env["PYTHONPATH"] = ""
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools",
                                                     "deploy_health.py"), "--help"],
                       capture_output=True, text=True, cwd=tempfile.gettempdir(),
                       env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "snapshot" in r.stdout, r.stdout


# ------------------------------------------------------- §6  the controls ---

def the_fake_backend_really_serves_what_it_is_told() -> None:
    """Every red above is only worth the fixture behind it."""
    with Fake([(200, orgs_body(THREE))]) as f:
        assert dh.served_orgs(f.port, 2) == ["alpha", "beta", "gamma"]
    with Fake([(200, orgs_body([]))]) as f:
        assert dh.served_orgs(f.port, 2) == []


def the_scripted_fake_really_advances() -> None:
    """The still-loading check is vacuous if the fake answers the same thing
    every time."""
    with Fake([(200, orgs_body([])), (200, orgs_body(["alpha"]))]) as f:
        first = dh.served_orgs(f.port, 2)
        second = dh.served_orgs(f.port, 2)
        third = dh.served_orgs(f.port, 2)
    assert first == [] and second == ["alpha"] and third == ["alpha"], (
        first, second, third)


def the_slug_filter_is_not_rejecting_everything() -> None:
    """`junk_in_the_orgs_directory_invents_no_orgs` would pass just as well if
    `slug_ok` returned False for every input."""
    for good in ("alpha", "org2", "a.b", "x_y-z", "con", "a" * 128):
        assert dh.slug_ok(good), good
    for bad in ("", ".hidden", "-lead", " pad", "a/b", "a\\b", "a\x00b", "a" * 129):
        assert not dh.slug_ok(bad), bad


print("\n== §1  what the data root says must exist ==")
check("a JSON root lists its orgs", json_backend_root_lists_its_orgs)
check("a migrated (SQLite) root lists the SAME orgs",
      sqlite_backend_root_lists_the_same_orgs)
check("a .db with no premigration beside it is still an org",
      a_db_with_no_premigration_beside_it_is_still_an_org)
check("a premigration alone still means the org exists",
      a_premigration_alone_still_means_the_org_exists)
check("the hand-annotated STALE premigration resolves to its slug",
      the_hand_annotated_premigration_on_this_machine_resolves)
check("junk in orgs/ invents no orgs", junk_in_the_orgs_directory_invents_no_orgs)
check("a lone .db.migrating invents nothing", a_lone_db_migrating_invents_nothing)
check("a fresh install expects nothing, and says so",
      a_fresh_install_expects_nothing_and_says_so)
check("an unreadable orgs/ raises rather than reporting zero",
      an_unreadable_orgs_directory_raises_rather_than_returning_empty)

print("\n== §2  the verdicts ==")
check("CONTROL: the old 200-only rule passes the fixture below",
      the_old_rule_would_have_passed_the_fixture_that_follows)
check("RED: up, 200, zero orgs served, three on disk",
      up_and_serving_zero_orgs_when_three_exist_is_RED)
check("RED: it names the deploy when the old backend was fine",
      it_names_the_deploy_as_the_culprit_when_the_old_backend_was_fine)
check("RED: it does NOT blame the deploy for a pre-existing fault",
      it_does_not_blame_the_deploy_for_a_pre_existing_fault)
check("RED: one missing org of three, named",
      one_missing_org_out_of_three_is_RED_and_is_named)
check("RED: an org served that is not on disk",
      an_org_served_that_is_not_on_disk_is_RED)
check("a backend that never answers is a different verdict",
      a_backend_that_never_answers_is_a_DIFFERENT_verdict)
check("a 200 with an unreadable body is up-and-wrong, not down",
      a_200_with_an_unreadable_body_is_up_and_wrong_not_down)
check("GREEN: a genuinely empty install", a_genuinely_empty_install_is_GREEN)
check("GREEN: a healthy deploy", a_healthy_deploy_is_GREEN)
check("GREEN: a backend still loading is not failed",
      a_backend_that_is_still_loading_is_NOT_failed)
check("RED: a missing snapshot fails even when everything else is perfect",
      a_missing_snapshot_fails_even_when_everything_else_is_perfect)
check("RED: an unreadable data root fails the deploy",
      an_unreadable_data_root_fails_the_deploy_rather_than_passing_it)
check("the verdicts survive the process boundary",
      the_exit_codes_survive_the_process_boundary)

print("\n== §3  the budgets ==")
check("it cannot hang when nothing ever answers",
      it_cannot_hang_the_deploy_when_nothing_ever_answers)
check("it cannot hang when the state never settles",
      it_cannot_hang_the_deploy_when_the_state_never_settles)

print("\n== §4  both deploy scripts read it ==")
check("update.ps1 and update.sh both run the check",
      both_deploy_scripts_run_the_check)
check("neither settles for a 200 any more",
      neither_deploy_script_settles_for_a_200)
check("both fail the deploy on a non-zero verdict",
      both_deploy_scripts_fail_on_a_nonzero_verdict)
check("both branch on every verdict, not just zero",
      both_deploy_scripts_distinguish_every_verdict)

print("\n== §5  a genuine second source of truth ==")
check("the checker imports no orgtree", the_checker_imports_no_orgtree)
check("the checker loads no orgtree module at runtime",
      the_checker_loads_no_orgtree_module_at_runtime)
check("the checker runs with the backend nowhere on the path",
      the_checker_runs_with_the_backend_nowhere_on_the_path)

print("\n== §6  controls ==")
check("the fake backend really serves what it is told",
      the_fake_backend_really_serves_what_it_is_told)
check("the scripted fake really advances", the_scripted_fake_really_advances)
check("the slug filter is not rejecting everything",
      the_slug_filter_is_not_rejecting_everything)

print("\n%s" % ("=" * 60))
if FAILED:
    print("FAILED %d / %d" % (len(FAILED), PASSED + len(FAILED)))
    for f in FAILED:
        print("  x %s" % f)
    sys.exit(1)
print("PASSED %d/%d" % (PASSED, PASSED))
