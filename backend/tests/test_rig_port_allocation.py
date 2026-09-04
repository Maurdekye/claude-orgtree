"""The turn-lifecycle rig allocates its port PER RUN, and still declares itself
inert when it cannot have one.

    python backend/tests/test_rig_port_allocation.py

WHAT THIS DEFENDS (2026-09-05). `test_turn_lifecycle` bound a FIXED port. On a
machine several agents share, two runs of it at once both took that number and
the loser did not fail cleanly — it passed the hermetic half and then failed
inside the live section with shaped, plausible mail-redelivery errors
("carried by [mailbox]" green, "the next turn delivers it" red) plus a dozen
cascading "section aborted" entries. Measured: rig dirs two seconds apart at
23:54:38 and 23:54:40. The resulting 158-pass/15-fail run was read as a
regression and nearly bought a revert of an unrelated commit.

The concurrency property is checked with two REAL simultaneous runs, in
`--hermetic` mode so it costs a second rather than 22 minutes. That is only
possible because the suite now REPORTS the port it took.
"""

import os
import re
import socket
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SUITE = os.path.join(ROOT, "backend", "tests", "test_turn_lifecycle.py")
#: the number that used to be hardcoded. Named here ONLY so the tests can
#: assert we no longer land on it by default.
OLD_FIXED = 7401
PORT_LINE = re.compile(r"^rig port: (\d+) \((.+?)\)$", re.M)

PASS = 0


def check(label, fn):
    global PASS
    fn()
    PASS += 1
    print(f"  ok {PASS:2d}  {label}")


def run(args, env_extra=None, timeout=300):
    env = dict(os.environ)
    env["ORGTREE_DATA"] = tempfile.mkdtemp(prefix="rigport-")
    env.update(env_extra or {})
    # ⚠ encoding/errors explicitly: the suite prints ⚠ and ☠, and the child
    # pipe would otherwise be decoded as cp1252 on this host and raise
    # UnicodeDecodeError inside subprocess's reader THREAD — which surfaces as
    # `out is None` here, not as a decode error, and reads like the suite
    # printed nothing.
    return subprocess.run([sys.executable, "-u", SUITE, *args],
                          capture_output=True, text=True, env=env,
                          encoding="utf-8", errors="replace",
                          timeout=timeout, cwd=ROOT)


def spawn(args):
    env = dict(os.environ)
    env["ORGTREE_DATA"] = tempfile.mkdtemp(prefix="rigport-")
    return subprocess.Popen([sys.executable, "-u", SUITE, *args],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace",
                            env=env, cwd=ROOT)


def reported(out):
    m = PORT_LINE.search(out)
    assert m, f"the suite did not report its port:\n{out[:600]}"
    return int(m.group(1)), m.group(2)


# ── the concurrency property, with two real simultaneous runs ──────────────

def two_concurrent_runs_get_different_ports():
    """THE POINT OF THE CHANGE. Two runs at once must not contend for one
    number. Hermetic mode so this costs a second; the allocation happens at
    import, before the mode is consulted, so it is the same allocation the
    live phase would use."""
    a, b = spawn(["--hermetic"]), spawn(["--hermetic"])
    outs = [p.communicate(timeout=300)[0] for p in (a, b)]
    for i, (p, o) in enumerate(zip((a, b), outs)):
        assert p.returncode == 0, f"run {i} exited {p.returncode}:\n{o[-800:]}"
    pa, ka = reported(outs[0])
    pb, kb = reported(outs[1])
    assert ka == kb == "ephemeral, per run", (ka, kb)
    assert pa != pb, f"BOTH RUNS TOOK THE SAME PORT {pa} — they would collide"
    for p in (pa, pb):
        assert p != OLD_FIXED, f"fell back to the old fixed port {p}"
        assert 1024 < p < 65536, p


def the_port_is_actually_free_when_reported():
    """An allocation that hands back a port somebody else holds is worse than
    a fixed one, because nothing downstream would expect it."""
    out = run(["--hermetic"]).stdout
    port, kind = reported(out)
    assert kind == "ephemeral, per run", kind
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))     # raises if it was never really free
    finally:
        s.close()


# ── the explicit override still works ──────────────────────────────────────

def an_explicit_port_is_honoured_and_labelled():
    """`--port` stays, for attaching a debugger or reproducing one exact rig —
    and it says it is explicit, because it is also the only way to get a
    collision back."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    want = int(s.getsockname()[1])
    s.close()
    out = run(["--hermetic", "--port", str(want)]).stdout
    got, kind = reported(out)
    assert got == want, (got, want)
    assert kind == "explicit --port", kind


# ── the inert declaration survives ─────────────────────────────────────────

def a_held_port_still_declares_DID_NOT_RUN():
    """⚠ THE PROPERTY THAT MADE ANY OF THIS VISIBLE. When the rig cannot have
    its port the suite must say it tested NOTHING live — not pass, and not
    report a product failure. This holds the port open and requires that
    declaration, so the guard cannot be quietly lost to the new allocation."""
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    held = int(s.getsockname()[1])
    s.listen(1)
    try:
        r = run(["--port", str(held)], timeout=600)
    finally:
        s.close()
    out = r.stdout + r.stderr
    assert "DID NOT RUN" in out, f"no inert declaration:\n{out[-1200:]}"
    assert "This is not a pass" in out, out[-600:]
    assert "tested NOTHING" in out, out[-600:]
    # …and it must name the cause that is actually true for an explicit port
    assert "ANOTHER RUN OF THIS SUITE" in out, (
        f"the cause line did not name concurrency:\n{out[-1200:]}")
    assert r.returncode != 0, "an inert run must not exit 0"


def the_cause_line_is_branched_not_boilerplate():
    """The old message named only 'an earlier run was KILLED, orphaning its
    backend' — which stopped being the usual cause once `_leash` (D-170) tied
    the rig to the suite process, and was NOT the cause on 2026-09-05. A guard
    that names the wrong cause misdirects exactly as badly as no guard."""
    # anchored on `port_free` itself, not on `class PortHeld` — `port_holder`
    # sits between them and a fixed-size window from the class stopped short
    # of the message, which would have made this pass on any file long enough
    src = open(SUITE, encoding="utf-8").read()
    i = src.index("def port_free(")
    body = src[i:src.index("\ndef ", i + 1)]
    assert "raise PortHeld(" in body, "wrong region — the raise is not here"
    assert "PORT_EXPLICIT else" in body, "the cause line is not branched"
    assert "ephemeral" in body, body[-500:]


# ── the fixed number is gone from the default path ─────────────────────────

def the_default_no_longer_names_a_fixed_port():
    """A text check with teeth: the old literal must not survive as the
    default anywhere in the argv handling."""
    src = open(SUITE, encoding="utf-8").read()
    i = src.index("PORT_EXPLICIT = ")
    decl = src[i:src.index("\n\n", i)]
    assert str(OLD_FIXED) not in decl, decl
    assert "_ephemeral_port()" in decl, decl


for label, fn in [
    ("two CONCURRENT runs get different ports (the real check)",
     two_concurrent_runs_get_different_ports),
    ("the reported port is genuinely free", the_port_is_actually_free_when_reported),
    ("--port is honoured and labelled explicit",
     an_explicit_port_is_honoured_and_labelled),
    ("a held port STILL declares DID NOT RUN, naming concurrency",
     a_held_port_still_declares_DID_NOT_RUN),
    ("the cause line is branched, not the old boilerplate",
     the_cause_line_is_branched_not_boilerplate),
    ("the default path names no fixed port",
     the_default_no_longer_names_a_fixed_port),
]:
    check(label, fn)

print(f"\nall {PASS} checks passed")
