"""Message-visibility suite, LIVE half — a real backend, a fake CLI, real time.

    A message the user sent is on screen CONTINUOUSLY from the moment it is
    sent until the conversation ends, and NEVER appears twice.

Run:  .venv/Scripts/python.exe backend/tests/test_message_visibility_live.py
      --quick        one repetition per configuration (smoke)
      --reps N       repetitions per configuration (default 3)
      --only <sub>   only configurations whose label contains <sub>
      --port N       bind the throwaway backend here (default: first free)

WHAT THIS ADDS OVER THE HERMETIC HALF
-------------------------------------
`test_message_visibility.py` drives the world step by step, so it can visit
orderings a race only reaches sometimes. This one gives up that control to buy
the thing it cannot fake: **real concurrency**. A real uvicorn process, real
threads, the real turn loop, the real `DOC_LOCK`, real `POST /message` →
`_journal_drain` → subprocess launch, and a 20 Hz poller that sees only what a
browser would see.

The CLI is `fakecli.js` (the supervisor lets one in via `ORGTREE_CLAUDE_CLI`),
which makes the D-55 window — drain → transcript echo — a NUMBER. Sweeping it
across the danger zone and repeating each point is what turns "sometimes" into
a measurement: a race that fails one time in twenty has to be run enough times
to be caught, and a fixed delay says exactly which side of the window each run
was on.

Nothing here touches the user's data: its own ORGTREE_DATA, its own HOME (the
transcripts land there), its own port, and every org is deleted at the end.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.normpath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, _HERE)

import msgvis                                                    # noqa: E402
from msgvis import Desk, token                                   # noqa: E402

REPS = int(sys.argv[sys.argv.index("--reps") + 1]) if "--reps" in sys.argv else 3
if "--quick" in sys.argv:
    REPS = 1
ONLY = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else ""

PASS = 0
FAIL: list[tuple[str, str]] = []
#: measured behaviours that BREAK the invariant for a reason outside this bug
#: family — reported loudly, never silently tolerated
EXCEPTIONS: list[tuple[str, str]] = []
#: fake-CLI-only orderings — see fragile()
FRAGILE: list[tuple[str, str, str]] = []
RUNS = 0
SAMPLES = 0
KEEP = "--keep" in sys.argv


def check(label: str, fn) -> None:
    global PASS
    if ONLY and ONLY not in label:
        return
    try:
        fn()
    except Exception:                                            # noqa: BLE001
        FAIL.append((label, traceback.format_exc()))
        print(f"  FAIL     {label}")
        return
    PASS += 1
    print(f"  ok {PASS:3d}  {label}")


def fragile(label: str, why_unreachable: str, fn) -> None:
    """A configuration the FAKE CLI can produce and the real one does not.

    Same contract as the hermetic suite's: `why_unreachable` must name a
    MEASUREMENT. These are the mechanism's fault lines — worth driving
    deliberately, because they say what would happen if the CLI ever
    changed, but they are not live bugs and must not read as failures."""
    global PASS
    if ONLY and ONLY not in label:
        return
    try:
        fn()
    except Exception as e:                                       # noqa: BLE001
        FRAGILE.append((label, why_unreachable,
                        str(e).splitlines()[0][:200]))
        print(f"  ⚠ FRAGILE {label}")
        return
    PASS += 1
    print(f"  ok {PASS:3d}  {label}")


# ------------------------------------------------------------------ the rig

TMP = tempfile.mkdtemp(prefix="orgtree-msgvis-live-")
HOME = os.path.join(TMP, "home")
DATA = os.path.join(TMP, "data")
CFG = os.path.join(TMP, "fakecli.json")
os.makedirs(HOME, exist_ok=True)
os.makedirs(DATA, exist_ok=True)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


PORT = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv \
    else free_port()
BASE = f"http://127.0.0.1:{PORT}"
PROC: subprocess.Popen[str] | None = None
LOG = os.path.join(TMP, "backend.log")


def set_cfg(**per_node) -> None:
    """Reprogram the fake CLI. Read fresh on every launch, so this takes effect
    on the NEXT turn without restarting anything."""
    cfg = {"default": per_node.pop("default", {})}
    cfg.update(per_node)
    with open(CFG, "w", encoding="utf-8") as f:
        json.dump(cfg, f)


def start_backend(max_turns: int = 16, steer_hook: str = "0",
                  real_cli: bool = False) -> None:
    global PROC
    stop_backend()
    env = dict(os.environ)
    env.update({
        "ORGTREE_DATA": DATA, "USERPROFILE": HOME, "HOME": HOME,
        "ORGTREE_PORT": str(PORT),
        "FAKECLI_CONFIG": CFG,
        "ORGTREE_MAX_TURNS": str(max_turns),
        "ORGTREE_STEER_HOOK": steer_hook,
        "ORGTREE_TURN_TIMEOUT": "60",
        "PYTHONPATH": os.path.join(_REPO, "backend"),
        "PYTHONIOENCODING": "utf-8",
        # ⚠ the throwaway backend must claim NOTHING the user's real one holds.
        # The admin listener is on a free ephemeral port, but the sandbox
        # BRIDGE listener defaults to 0.0.0.0:7362 and a second bind kills the
        # whole process at startup (`sys.exit(STARTUP_FAILURE)` inside uvicorn,
        # which reads as "the backend just never came up").
        "ORGTREE_BRIDGE_PORT": "0",
    })
    if real_cli:
        # let the supervisor resolve the real binary (the pinned agent install
        # first, then PATH) — the `--real-cli` runs measure the CLI itself.
        #
        # ⚠ and give it the REAL home. The CLI's credentials live in
        # ~/.claude, so a redirected HOME produces turns that die in ~1.5 s
        # with no transcript at all — which looks like a passing run (the
        # pending row covers the whole doomed turn) and measures nothing.
        # Transcripts therefore land in the user's own ~/.claude/projects,
        # exactly as a normal orgtree turn does; ORGTREE_DATA stays isolated.
        env.pop("ORGTREE_CLAUDE_CLI", None)
        for k in ("USERPROFILE", "HOME"):
            if os.environ.get(k):
                env[k] = os.environ[k]
        # ⚠ and name the binary explicitly. The supervisor's own resolution
        # prefers the PINNED install under the DATA root — which this rig has
        # redirected to a temp dir — so it falls through to `which claude`,
        # finds the npm `claude.CMD` shim, and on Windows that is launched as
        # `cmd /c`, which TRUNCATES argv at the first embedded newline. The
        # identity prompt is multiline, so every turn died in ~1.6 s with no
        # transcript and no error (the supervisor's own warning, CLAUDE.md §4).
        pin = os.path.join(
            os.path.expanduser(os.environ.get("ORGTREE_REAL_DATA", "~/orgtree")),
            "cli", "node_modules", "@anthropic-ai", "claude-code", "bin",
            "claude.exe" if os.name == "nt" else "claude")
        if os.path.exists(pin):
            env["ORGTREE_CLAUDE"] = pin
    else:
        env["ORGTREE_CLAUDE_CLI"] = os.path.join(_HERE, "fakecli.js")
    env.pop("ORGTREE_PUBLIC_PORT", None)
    env.pop("ORGTREE_EXPOSE_ADMIN", None)
    log = open(LOG, "a", encoding="utf-8")
    PROC = subprocess.Popen(
        [sys.executable, "-m", "orgtree.api"], cwd=os.path.join(_REPO, "backend"),
        env=env, stdout=log, stderr=log, text=True)
    for _ in range(200):
        if PROC.poll() is not None:
            raise RuntimeError(
                f"backend exited with {PROC.returncode} during startup; "
                f"log tail:\n" + _log_tail())
        try:
            urllib.request.urlopen(BASE + "/api/orgs", timeout=1).read()
            return
        except Exception:                                        # noqa: BLE001
            time.sleep(0.1)
    raise RuntimeError(f"backend did not come up on {PORT}:\n" + _log_tail())


def _log_tail(n: int = 2500) -> str:
    try:
        return open(LOG, encoding="utf-8", errors="replace").read()[-n:]
    except OSError:
        return "(no log)"


def stop_backend() -> None:
    global PROC
    if PROC is None:
        return
    PROC.terminate()
    try:
        PROC.wait(timeout=10)
    except subprocess.TimeoutExpired:
        PROC.kill()
    PROC = None


def api(method: str, path: str, body=None, timeout: float = 30):
    req = urllib.request.Request(
        BASE + path, method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
    return json.loads(raw) if raw else {}


# ------------------------------------------------------------------- orgs

_orgs: list[str] = []


def make_org(label: str, agents: int = 1) -> tuple[str, list[str]]:
    name = f"zz vislive {len(_orgs)} {label}"[:60]
    r = api("POST", "/api/orgs", {"name": name})
    slug = r.get("slug") or r.get("org", {}).get("slug")
    _orgs.append(slug)
    nids = []
    for i in range(agents):
        h = api("POST", f"/api/orgs/{slug}/ops",
                {"op": "hire", "actor": msgvis.USER, "parent": None,
                 "tier": "haiku", "grant": 2, "name": f"agent{i}",
                 "charter": "a test agent",
                 "tools": {"bash": True, "web": False, "edit": False,
                           "subagents": False, "mcp": []},
                 "org_visibility": "team", "add_dirs": []})
        nids.append(h.get("node") or f"agent{i}")
    return slug, nids


def drop_orgs() -> None:
    for slug in list(_orgs):
        try:
            api("DELETE", f"/api/orgs/{slug}")
        except Exception:                                        # noqa: BLE001
            pass


# ------------------------------------------------------------- the probe

class Probe:
    """A desk watching one node at 20 Hz, scoring every payload it sees.

    20 Hz because the hole this suite exists to catch was 0.25 s wide once
    (D-50) and ~1 s wide the last time (D-55) — a per-sample round trip an
    order of magnitude slower than the event would simply miss it."""

    HZ = 20.0

    def __init__(self, slug: str, nid: str, probe_text: str, label: str):
        self.slug, self.nid, self.probe, self.label = slug, nid, probe_text, label
        self.desk = Desk()
        self.samples: list[tuple[float, dict[str, int], bool]] = []
        self.stop = threading.Event()
        self.err: str | None = None
        self.t0 = time.time()
        self.th = threading.Thread(target=self._loop, daemon=True)

    def _fetch(self) -> dict:
        return api("GET", f"/api/orgs/{self.slug}/nodes/{self.nid}/chat"
                          f"?last={msgvis.CHAT_WINDOW}", timeout=20)

    def prime(self) -> None:
        """The payload the ghost's baseline is taken from — exactly what the
        desk has in hand at the moment the user hits send."""
        self.desk.fetch(self._fetch())

    def send(self, body: str) -> None:
        self.desk.send(body)                 # convo.ts addPending, pre-POST
        self.t0 = time.time()
        self.th.start()

    def _loop(self) -> None:
        global SAMPLES
        while not self.stop.is_set():
            t = time.time()
            try:
                c = self._fetch()
            except Exception as e:                               # noqa: BLE001
                self.err = self.err or f"fetch failed: {e}"
                time.sleep(0.2)
                continue
            self.desk.fetch(c)
            r = self.desk.renders(self.probe)
            with threading.Lock():
                self.samples.append((t - self.t0, r, bool(c.get("busy"))))
                SAMPLES += 1
            time.sleep(max(0.0, 1.0 / self.HZ - (time.time() - t)))

    def finish(self) -> None:
        self.stop.set()
        self.th.join(timeout=10)

    def verdict(self) -> None:
        """Every sample must show the message exactly once."""
        if self.err:
            raise AssertionError(f"{self.label}: {self.err}")
        if not self.samples:
            raise AssertionError(f"{self.label}: no samples taken")
        bad = [(t, r) for t, r, _ in self.samples if r["total"] != 1]
        if bad:
            gaps = [(t, r) for t, r in bad if r["total"] == 0]
            dups = [(t, r) for t, r in bad if r["total"] > 1]
            span = (max(t for t, _ in bad) - min(t for t, _ in bad))
            raise AssertionError(
                f"{self.label}: {len(gaps)} GAP + {len(dups)} DUPLICATE samples "
                f"out of {len(self.samples)} (worst span {span:.2f} s)\n"
                f"  first gap  at t+{gaps[0][0]:.2f}s  {gaps[0][1]}\n"
                if gaps else
                f"{self.label}: {len(dups)} DUPLICATE samples out of "
                f"{len(self.samples)}, span {span:.2f} s\n"
                f"  first at t+{dups[0][0]:.2f}s  {dups[0][1]}\n")

    def summary(self) -> str:
        on = [t for t, r, _ in self.samples if r["total"] >= 1]
        return (f"{len(self.samples)} samples over "
                f"{self.samples[-1][0]:.2f}s, on screen "
                f"{100.0 * len(on) / len(self.samples):.0f}% of them")

    def timings(self) -> dict[str, float | int | None]:
        """When each carrier held the message — the numbers that say WHERE in
        the hand-off a run actually was, rather than just whether it passed."""
        def first(pred) -> float | None:
            return next((t for t, r, _ in self.samples if pred(r)), None)

        def last(pred) -> float | None:
            return next((t for t, r, _ in reversed(self.samples) if pred(r)), None)
        both = [t for t, r, _ in self.samples
                if r["pendrow"] >= 1 and r["transcript"] >= 1]
        none = [t for t, r, _ in self.samples if r["total"] == 0]
        return {
            "samples": len(self.samples),
            "ghost_until": last(lambda r: r["ghost"] >= 1),
            "pendrow_from": first(lambda r: r["pendrow"] >= 1),
            "pendrow_until": last(lambda r: r["pendrow"] >= 1),
            "transcript_from": first(lambda r: r["transcript"] >= 1),
            "overlap_s": (max(both) - min(both)) if both else 0.0,
            "gap_s": (max(none) - min(none)) if none else 0.0,
            "gap_samples": len(none),
        }


def wait_idle(slug: str, nid: str, secs: float = 45) -> None:
    end = time.time() + secs
    while time.time() < end:
        c = api("GET", f"/api/orgs/{slug}/nodes/{nid}/chat?last=5")
        if not c.get("busy"):
            return
        time.sleep(0.2)


# ---------------------------------------------------------------- scenarios

def one_send(label: str, *, echo_ms: int, first_ms: int | None = None,
             body: str | None = None, probe: str = "", prebusy: bool = False,
             echo_after: bool = False, tools: int = 0, think_ms: int = 0,
             start_ms: int = 0, org=None) -> Probe:
    """The canonical live send: prime the desk, create the ghost, POST, and
    watch at 20 Hz until the turn is over.

    ⚠ `probe` must be given whenever `body` is: the payload TRUNCATES pending
    bodies, so scoring a long message by its full text finds it in the
    transcript and not in the pending row, and reports a gap that is really a
    defect in the probe. Every corpus entry carries a short probe for exactly
    this reason."""
    global RUNS
    RUNS += 1
    t = token()
    body = body or f"hello {t}"
    probe_text = probe or (t if t in body else body)
    set_cfg(default={"echoMs": echo_ms,
                     "firstEventMs": first_ms if first_ms is not None
                     else echo_ms + 300,
                     "echoAfterFirstEvent": echo_after,
                     "startMs": start_ms, "tools": tools, "thinkMs": think_ms,
                     "resultMs": 30})
    slug, nids = org if org else make_org(label)
    nid = nids[0]
    if prebusy:
        # a turn is already running, so the send lands on a BUSY agent: it
        # queues (or steers) instead of starting, which is exactly the state
        # that widens the window
        api("POST", f"/api/orgs/{slug}/nodes/{nid}/message",
            {"text": f"warmup {token()}"})
        time.sleep(0.4)
    p = Probe(slug, nid, probe_text, label)
    p.prime()
    p.send(body)
    try:
        api("POST", f"/api/orgs/{slug}/nodes/{nid}/message", {"text": body})
        wait_idle(slug, nid)
        time.sleep(0.6)          # …and a moment after the turn, still on screen
    finally:
        p.finish()
    p.verdict()
    return p


def main() -> None:
    print(f"live rig: port {PORT}, data {DATA}")
    set_cfg(default={})
    start_backend()
    try:
        print("\nsmoke:")

        def _smoke():
            p = one_send("smoke", echo_ms=400)
            assert len(p.samples) > 10, p.samples
        check("a plain send is visible for every sample of its turn", _smoke)

        # ---- THE SWEEP: the D-55 window, measured, across the danger zone
        print("\nthe drain→echo window swept (D-55's race, as a dial):")
        for ms in (0, 50, 150, 400, 800, 1500, 3000):
            for rep in range(REPS):
                check(f"window · echo after {ms} ms · rep {rep + 1}",
                      lambda m=ms: one_send(f"win{m}", echo_ms=m))

        # ⚠ FRAGILITY, not a bug. `_confirm_delivered` retires the delivery
        # journal on the first non-`system` STDOUT event, while the desk
        # renders from the TRANSCRIPT FILE — two different pieces of evidence
        # about the same fact. The fake CLI is told to invert them here, which
        # opens a hole exactly `ms` wide. The real CLI does not, and that is
        # measured rather than assumed. Driven anyway, because the day the CLI
        # changes this becomes the next instance of the family.
        print("\n…with the transcript echo LAST (stdout confirms first):")
        _WHY = ("the real CLI never does this: 0 gap samples in 1 654 scored "
                "samples over 10 real turns (--real-cli), and 115/115 turn "
                "echoes precede the first assistant record across 94 real "
                "transcripts")
        for ms in (200, 800, 2000):
            for rep in range(REPS):
                fragile(f"window · transcript lags the confirming stdout event "
                        f"by {ms} ms · rep {rep + 1}", _WHY,
                        lambda m=ms: one_send(f"after{m}", echo_ms=m,
                                              first_ms=300, echo_after=True))

        print("\n…with a slow process start (cold CLI / container boot):")
        for ms in (500, 2000):
            for rep in range(REPS):
                check(f"window · {ms} ms before the CLI says anything · "
                      f"rep {rep + 1}",
                      lambda m=ms: one_send(f"start{m}", echo_ms=300, start_ms=m))

        print("\n…while the agent is already busy (queued behind a turn):")
        for ms in (100, 800):
            for rep in range(REPS):
                check(f"busy · echo {ms} ms · rep {rep + 1}",
                      lambda m=ms: one_send(f"busy{m}", echo_ms=m, prebusy=True,
                                            think_ms=200))

        print("\n…with a long think before the first token (sealed reasoning):")
        for rep in range(REPS):
            check(f"think · 1.5 s of thinking first · rep {rep + 1}",
                  lambda: one_send("think", echo_ms=200, think_ms=1500))

        # ---- text axis, live
        print("\nthe text axis, live:")
        for label, body, probe in msgvis.text_variants(token()):
            if not probe.strip():
                continue    # a whitespace probe cannot be counted by
                            # containment — it occurs in every envelope. The
                            # case it exists for (post_mail's IndexError on a
                            # whitespace-only body) is asserted hermetically.
            check(f"text · {label}",
                  lambda b=body, pr=probe, l=label: one_send(
                      f"txt-{l}", echo_ms=300, body=b, probe=pr))

        # ---- mid-task steering, with the hook actually firing
        print("\nmid-task steering (the PostToolUse hook really runs):")
        start_backend(steer_hook="1")
        for rep in range(REPS):
            def _steer():
                global RUNS
                RUNS += 1
                t = token()
                body = f"steer me {t}"
                set_cfg(default={"echoMs": 200, "firstEventMs": 300,
                                 "tools": 4, "toolMs": 400, "resultMs": 50})
                slug, nids = make_org("steer")
                nid = nids[0]
                api("POST", f"/api/orgs/{slug}/nodes/{nid}/message",
                    {"text": f"warmup {token()}"})
                time.sleep(0.9)          # mid-response, between tool calls
                p = Probe(slug, nid, t, "steer")
                p.prime()
                p.send(body)
                try:
                    api("POST", f"/api/orgs/{slug}/nodes/{nid}/message",
                        {"text": body})
                    wait_idle(slug, nid)
                    time.sleep(0.8)
                finally:
                    p.finish()
                p.verdict()
            check(f"steer · delivered mid-task by the hook · rep {rep + 1}",
                  _steer)
        start_backend(steer_hook="0")

        # ---- turn-slot saturation: the turn CANNOT start
        print("\nturn-slot saturation (the window at its widest):")

        def _saturated():
            global RUNS
            RUNS += 1
            start_backend(max_turns=1)
            try:
                set_cfg(default={"echoMs": 200, "firstEventMs": 300,
                                 "thinkMs": 2500})
                slug, nids = make_org("sat", agents=2)
                blocker, victim = nids[0], nids[1]
                api("POST", f"/api/orgs/{slug}/nodes/{blocker}/message",
                    {"text": f"hog the slot {token()}"})
                time.sleep(0.5)
                t = token()
                body = f"waiting for a slot {t}"
                p = Probe(slug, victim, t, "slot-saturated")
                p.prime()
                p.send(body)
                try:
                    api("POST", f"/api/orgs/{slug}/nodes/{victim}/message",
                        {"text": body})
                    wait_idle(slug, victim, secs=60)
                    time.sleep(0.6)
                finally:
                    p.finish()
                p.verdict()
            finally:
                start_backend()
        for rep in range(max(1, REPS - 1)):
            check(f"saturation · the turn waits for a slot · rep {rep + 1}",
                  _saturated)

        # ---- several agents at once
        print("\nconcurrent sends to several agents:")

        def _concurrent():
            global RUNS
            set_cfg(default={"echoMs": 300, "firstEventMs": 500, "thinkMs": 300})
            slug, nids = make_org("conc", agents=4)
            probes = []
            for nid in nids:
                t = token()
                p = Probe(slug, nid, t, f"concurrent {nid}")
                p.prime()
                probes.append((p, nid, f"hello {t}"))
            RUNS += len(probes)
            for p, _, body in probes:
                p.send(body)
            threads = [threading.Thread(
                target=lambda n=nid, b=body: api(
                    "POST", f"/api/orgs/{slug}/nodes/{n}/message", {"text": b}))
                for (_, nid, body) in probes]
            for th in threads:
                th.start()
            for th in threads:
                th.join()
            for _, nid, _ in probes:
                wait_idle(slug, nid)
            time.sleep(0.6)
            for p, _, _ in probes:
                p.finish()
            for p, _, _ in probes:
                p.verdict()
        for rep in range(max(1, REPS - 1)):
            check(f"concurrent · 4 agents messaged at once · rep {rep + 1}",
                  _concurrent)

        # ---- the turn dies
        print("\nthe turn fails (the message must survive its delivery):")

        def _crash(echo_first: bool):
            """The CLI dies mid-turn. Two shapes, and they are NOT the same:

            • it dies before writing anything — the journal folds the mail back
              into the mailbox and the pending row is the only carrier. Clean.
            • it dies AFTER writing the user record but BEFORE its first
              non-`system` stdout event — the transcript already shows the
              message, yet `_confirm_delivered` never ran, so the fold-back
              re-queues it and the desk shows it TWICE, indefinitely.

            The second is a deliberate consequence of at-least-once delivery
            (`_confirm_delivered`'s C1 rule: a successful pipe write is not
            consumption), not of this bug family — but it is a real duplicate
            on screen, so it is measured rather than assumed away."""
            global RUNS
            RUNS += 1
            t = token()
            body = f"this turn will die {t}"
            # the death is on its OWN clock, so `echo_first` decides only
            # whether the transcript record beat it
            set_cfg(default={"echoMs": 50 if echo_first else 4000,
                             "firstEventMs": 9000, "crashAtMs": 400})
            slug, nids = make_org("crash")
            nid = nids[0]
            p = Probe(slug, nid, t, "crashing turn")
            p.prime()
            p.send(body)
            try:
                api("POST", f"/api/orgs/{slug}/nodes/{nid}/message", {"text": body})
                wait_idle(slug, nid)
                time.sleep(1.0)
            finally:
                p.finish()
            return p
        for rep in range(max(1, REPS - 1)):
            check(f"crash · the CLI dies before writing anything · rep {rep + 1}",
                  lambda: _crash(False).verdict())

        def _crash_after_echo():
            p = _crash(True)
            tm = p.timings()
            assert tm["gap_s"] == 0, f"a GAP, not the expected duplicate: {tm}"
            # PROMOTED 2026-08-04, on its own instruction ("if the fold-back
            # learned to consult the transcript, delete this exception and
            # assert the invariant"). It did not learn — weakening the
            # fold-back would cost the agent ever being re-asked — but
            # `node_chat` now applies its `_in_transcript` evidence test to the
            # MAILBOX rows as well as the journal's, so the duplicate is gone
            # at the display layer with delivery untouched. The turn-lifecycle
            # suite's twin of this check was promoted at the same time.
            dups = [t for t, r, _ in p.samples if r["total"] > 1]
            assert not dups, (
                f"the transcript+mailbox double-render is BACK: {len(dups)} of "
                f"{len(p.samples)} samples showed it twice, from t+{dups[0]:.2f}s")
        check("crash · after the echo: exactly one copy on screen",
              _crash_after_echo)

        def _usage_limit():
            global RUNS
            RUNS += 1
            t = token()
            body = f"this hits the limit {t}"
            set_cfg(default={"echoMs": 200, "firstEventMs": 300,
                             "usageLimit": True})
            slug, nids = make_org("limit")
            nid = nids[0]
            p = Probe(slug, nid, t, "usage limit")
            p.prime()
            p.send(body)
            try:
                api("POST", f"/api/orgs/{slug}/nodes/{nid}/message", {"text": body})
                wait_idle(slug, nid)
                time.sleep(1.0)
            finally:
                p.finish()
            p.verdict()
        check("usage limit · the frozen turn keeps its message on screen",
              _usage_limit)

        # ---- a burst of sends
        print("\na burst of sends at one agent:")

        def _burst(n: int = 8):
            global RUNS
            RUNS += n
            set_cfg(default={"echoMs": 200, "firstEventMs": 400, "thinkMs": 900})
            slug, nids = make_org("burst")
            nid = nids[0]
            probes = []
            for i in range(n):
                t = token()
                body = f"burst {i} {t}"
                p = Probe(slug, nid, t, f"burst {i}")
                p.prime()
                probes.append((p, body))
                p.send(body)
                api("POST", f"/api/orgs/{slug}/nodes/{nid}/message", {"text": body})
                time.sleep(0.15)
            wait_idle(slug, nid, secs=90)
            time.sleep(0.8)
            for p, _ in probes:
                p.finish()
            for p, _ in probes:
                p.verdict()
        for rep in range(max(1, REPS - 1)):
            check(f"burst · 8 sends 150 ms apart · rep {rep + 1}", _burst)

    finally:
        try:
            drop_orgs()
            left = [o["slug"] for o in api("GET", "/api/orgs")]
            assert not [s for s in left if s.startswith("zz-vislive")], left
        except Exception as e:                                   # noqa: BLE001
            print(f"  ⚠ cleanup check failed: {e}")
        stop_backend()

    print()
    if FAIL:
        print("=" * 72)
        for label, tb in FAIL:
            print(f"\nFAILED: {label}\n{tb}")
        print("=" * 72)
    if FRAGILE:
        print("KNOWN FRAGILITY — the fake CLI was told to do something the "
              "real one does not; each line names the measurement that says "
              "so:")
        for label, why, err in FRAGILE:
            print(f"  ⚠ {label}\n      unreachable because: {why}\n"
                  f"      breaks as: {err}")
        print()
    if EXCEPTIONS:
        print("MEASURED EXCEPTIONS — the invariant does not hold here, for a "
              "reason that is not this bug family:")
        for what, detail in EXCEPTIONS:
            print(f"  ⚠ {what}\n      {detail}")
        print()
    print(f"{PASS} checks passed, {len(FAIL)} failed, "
          f"{len(FRAGILE)} known-fragile, {len(EXCEPTIONS)} measured "
          f"exceptions ({RUNS} live turns, {SAMPLES} payload samples scored)")
    if FAIL:
        print(f"\n{len(FAIL)} CHECKS FAILED  (backend log: {LOG})")
        sys.exit(1)
    print(f"\nALL {PASS} CHECKS PASS")


def real_cli_main() -> None:
    """`--real-cli`: the same probe, driving the REAL Claude Code CLI.

    Everything else in this file substitutes the CLI so timing can be a dial.
    Three questions cannot be answered that way, because they are facts about
    the CLI rather than about orgtree, and each one decides whether a fault
    line found in the hermetic suite is live or theoretical:

      ① Does the real CLI's first stdout event ever beat its own transcript
        write? `_confirm_delivered` retires the delivery journal on that event,
        while the desk renders from the transcript — if stdout wins, the
        message is briefly in no carrier at all.
      ② What is the real drain→echo window, cold and warm? That is the hole
        D-55 closed; its width is the exposure if it ever reopens.
      ③ Does mid-task steering leave the message visible once the hook has
        taken it? The hook's text reaches the transcript as a record shape
        `read_chat` cannot render, so the steered log is the only carrier.

    ⚠ Real turns, real cost. Tier is haiku (never fable — explicit ruling).
    """
    global PASS
    print(f"live rig (REAL CLI): port {PORT}, data {DATA}")
    env_note = os.environ.get("ORGTREE_CLAUDE") or "(pinned/PATH resolution)"
    print(f"CLI: {env_note}")
    start_backend(steer_hook="1", real_cli=True)
    runs: list[tuple[str, dict]] = []
    try:
        def one_real(label: str, *, body: str, prebusy_tools: bool = False,
                     org=None):
            global RUNS
            RUNS += 1
            t = token()
            text = body.replace("{TOK}", t)
            slug, nids = org if org else make_org(label)
            nid = nids[0]
            if prebusy_tools:
                api("POST", f"/api/orgs/{slug}/nodes/{nid}/message",
                    {"text": "Run three separate bash commands that each "
                             "echo a number, one at a time, then stop."})
                time.sleep(6)             # mid-response, between tool calls
            p = Probe(slug, nid, t, label)
            p.prime()
            p.send(text)
            try:
                api("POST", f"/api/orgs/{slug}/nodes/{nid}/message", {"text": text})
                wait_idle(slug, nid, secs=180)
                time.sleep(1.0)
            finally:
                p.finish()
            runs.append((label, p.timings()))
            print(f"      {label}: {p.timings()}")
            tm = p.timings()
            assert tm["transcript_from"] is not None, (
                f"{label}: the turn produced no transcript bubble at all in "
                f"{tm['samples']} samples — the CLI is failing (bad auth? bad "
                f"argv?), so this run measures nothing. Check the backend log: "
                f"{LOG}")
            p.verdict()
            return slug, nids

        print("\ncold start (a brand-new session — the widest real window):")
        for rep in range(REPS):
            check(f"real · cold start · rep {rep + 1}",
                  lambda: one_real("cold", body="Reply with exactly: ok {TOK}"))

        print("\nwarm (a second message into the same live session):")
        for rep in range(max(1, REPS - 1)):
            def _warm():
                slug, nids = one_real("warm-1",
                                      body="Reply with exactly: ok {TOK}")
                wait_idle(slug, nids[0], secs=120)
                one_real("warm-2", body="Reply with exactly: again {TOK}",
                         org=(slug, nids))
            check(f"real · warm second message · rep {rep + 1}", _warm)

        print("\nmid-task (steered into a responding agent):")
        for rep in range(max(1, REPS - 1)):
            check(f"real · steered mid-task · rep {rep + 1}",
                  lambda: one_real("steered", prebusy_tools=True,
                                   body="Also note this: {TOK}"))

        print("\na long message (past every truncation the payload applies):")
        check("real · a 6 kB message",
              lambda: one_real("long", body="{TOK} " + ("context line. " * 450)
                               + " Reply with exactly: ok"))
    finally:
        try:
            drop_orgs()
        except Exception as e:                                   # noqa: BLE001
            print(f"  ⚠ cleanup failed: {e}")
        stop_backend()
    print("\nmeasured windows (seconds from the send):")
    for label, tm in runs:
        print(f"  {label:10s} pendrow {tm['pendrow_from']}→{tm['pendrow_until']} "
              f"· transcript from {tm['transcript_from']} · "
              f"overlap {tm['overlap_s']:.2f}s · gap {tm['gap_s']:.2f}s "
              f"({tm['gap_samples']} samples)")
    print()
    if FAIL:
        for label, tb in FAIL:
            print(f"\nFAILED: {label}\n{tb}")
    print(f"{PASS} checks passed, {len(FAIL)} failed "
          f"({RUNS} real turns, {SAMPLES} payload samples scored)")
    if FAIL:
        sys.exit(1)
    print(f"\nALL {PASS} CHECKS PASS")


if __name__ == "__main__":
    try:
        real_cli_main() if "--real-cli" in sys.argv else main()
    finally:
        stop_backend()
        if KEEP or FAIL:
            print(f"(kept the rig for inspection: {TMP})")
        else:
            shutil.rmtree(TMP, ignore_errors=True)
