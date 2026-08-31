"""D-201: the warm CLI process pool.

Orgtree historically started a brand-new CLI process for every single turn,
which made everything an interactive harness reads once at startup — the MCP
handshake, the system prompt, the working-directory scan — into something
re-read and re-sent per turn. Measured cost (cache-misses' audit, 2026-08-30):
agents cold on 44.3% of quiet turns vs 0.2% interactive, ~200k tokens re-sent
per cold resume.

This module keeps ONE parked CLI process per live agent, spawned at boot and
on hire, and hands it to `_run_one_turn` when a turn arrives. The rules are
the user's, verbatim where it matters:

  · processes only end on RETIREMENT, an explicit SYSTEM-PROMPT CHANGE, or
    orgtree SHUTDOWN (the job-object leash covers shutdown). No idle reaping,
    no cap eviction, no tidiness. Anything else killing a parked process is a
    defect.
  · a prompt change respawns the process IMMEDIATELY, in the background —
    never "mark dirty and fix it when a turn comes". The one exception: an
    agent MID-TURN is never disturbed; its re-warm happens at turn end.
  · orgtree adds no explicit MCP-handshake barrier (user ruling 2026-08-30).
    The CLI's `alwaysLoad` setting can nevertheless hold a cold or too-young
    process's turn-1 prompt for its connection timeout; the production audit
    measured that wait and the retain/revert policy is still open.

THE WARM PROCESS IS A CACHE, NEVER THE SOURCE OF TRUTH. Every caller falls
back to today's spawn-per-turn when the pool has nothing valid, and the agent
notices nothing. Correctness over speed: a process whose rendered identity
hash no longer matches is killed, not served — a stale system prompt would
mean a retool or grant silently not applying.

INVALIDATION IS A HASH, NOT AN EVENT LIST (coordinator ruling). We hash the
rendered identity prompt (including native session-start instruction files),
the spawn argv, the resolved credential identity and the explicit per-node env
override — the full set of inputs the process bakes in at spawn that may change
at runtime. An enumerated list of invalidating events is exactly what goes
stale when someone adds a surface; the audit found surfaces nobody had
enumerated.

KILL SWITCH (cache-misses' A/B requirement): ORGTREE_WARM env sets the
default — which is ON (user ruling 2026-08-30). The file
<ORGTREE_DATA>/warm.flag overrides the env AT RUNTIME without a rebuild —
write "0"/"1", or JSON {"enabled": bool, "exclude": ["slug/nid"]} for
per-agent arms; the D-203 settings toggle writes the same file through
set_enabled(). Checked cheaply (mtime cache) on every decision.

TELEMETRY: append-only JSONL at <ORGTREE_DATA>/journals/warm.jsonl, in the
exact shape cache-misses registered (admit / proc / pool / cache-break lines).
Deliberately NOT in the org document (DOC_LOCK contention) and NOT in scratch
or transcripts (which would dirty the very prompts being measured).
"""
from __future__ import annotations

import collections
import hashlib
import json
import os
import queue
import re
import subprocess
import threading
import time
from typing import Any, Iterator

from . import store

# ── knobs ──────────────────────────────────────────────────────────────────
# how often the keeper re-checks every live agent's hash even with no poke.
# File-borne surfaces (a granted or native CLAUDE.md edited, the MCP registry
# changed) never call store.save_org, so polling is what catches them;
# org-borne changes arrive faster via the save-hook poke.
WARM_POLL = float(os.environ.get("ORGTREE_WARM_POLL", "20"))
# cascade pacing: one team_charter edit can dirty a whole subtree at once, and
# this machine already has port contention — at most this many spawns run
# concurrently; the rest queue behind the gate, still "immediate" per agent.
SPAWN_PACE = max(1, int(os.environ.get("ORGTREE_WARM_SPAWN_PACE", "2")))
# pool snapshot cadence for the telemetry journal
POOL_SNAP_EVERY = 60.0

_FLAG_CACHE: dict[str, Any] = {"at": 0.0, "mtime": None, "val": None}
_FLAG_TTL = 2.0
_FLAG_LOCK = threading.RLock()  # atomic read/modify/write for runtime controls


def _flag_path() -> str:
    return os.path.join(store.DATA_ROOT, "warm.flag")


def _read_flag_unlocked() -> dict[str, Any] | None:
    """The runtime override: {enabled, exclude, malformed} — or None when no
    flag file exists. A file that exists but cannot be parsed (empty,
    truncated mid-write, bad JSON) comes back {"malformed": True}: what the
    system DOES with that (fall to the env) and what the measurement RECORDS
    (arm unknown, never guessed) are two separate questions — cache-misses'
    contract, coordinator-backed. Cached ~2 s by mtime so per-decision reads
    cost a stat, not a parse."""
    now = time.time()
    if now - _FLAG_CACHE["at"] < _FLAG_TTL:
        return _FLAG_CACHE["val"]
    p = _flag_path()
    try:
        mt = os.path.getmtime(p)
    except OSError:
        _FLAG_CACHE.update(at=now, mtime=None, val=None)
        return None
    if mt == _FLAG_CACHE["mtime"]:
        _FLAG_CACHE["at"] = now
        return _FLAG_CACHE["val"]
    val: dict[str, Any]
    try:
        raw = open(p, encoding="utf-8").read().strip()
        if raw in ("0", "false", "off"):
            val = {"enabled": False, "exclude": [], "malformed": False}
        elif raw in ("1", "true", "on"):
            val = {"enabled": True, "exclude": [], "malformed": False}
        elif not raw:
            # an EMPTY file is a torn write, not an opinion
            val = {"malformed": True}
        else:
            d = json.loads(raw)
            val = {"enabled": bool(d.get("enabled", True)),
                   "exclude": [str(x) for x in (d.get("exclude") or [])],
                   "malformed": False}
    except OSError:
        val = {"malformed": True}       # unreadable ≠ absent: arm unknown
    except ValueError:
        val = {"malformed": True}
    _FLAG_CACHE.update(at=now, mtime=mt, val=val)
    return val


def _read_flag() -> dict[str, Any] | None:
    """A coherent flag observation across an internal concurrent write."""
    with _FLAG_LOCK:
        return _read_flag_unlocked()


def set_flag(content: str) -> None:
    """Atomic flag write (temp + replace) for anything that flips the switch
    programmatically — a reader can never observe a torn write through this
    path. Hand-edits remain possible; the malformed handling above is what
    covers those."""
    with _FLAG_LOCK:
        p = _flag_path()
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
        _FLAG_CACHE["at"] = 0.0   # the very next decision sees this write


def warm_decision() -> tuple[bool, bool | None]:
    """ONE flag read → (what the system does, what the measurement records).
    The label is the enabled value when the source was trustworthy, and None
    when the flag file was malformed — the behaviour falls to the env there,
    but labelling that arm would be a guess, and a guessed arm is silent
    misattribution in the A/B. Callers journal THIS label, never a re-read:
    the flag can flip between a decision and a later read, and an admit row
    labelled with an arm the turn was not served under is worse than no row."""
    # ⚠ THE DEFAULT IS ON (user ruling at merge, 2026-08-30: "warming on by
    # default, though if you like, toggleable in the new app-wide
    # settings"). A fresh deploy warms every eligible agent at boot. The
    # runtime flag below is therefore the ONLY control arm for the A/B and
    # the only back-out that needs no redeploy — and the user-facing
    # settings toggle (D-203) is THE SAME UNDERLYING VALUE, written through
    # set_enabled(): the preference and the lever are deliberately one
    # piece of state, so do not "deduplicate" them apart later. A flip
    # takes effect at the next keeper pass — within ORGTREE_WARM_POLL
    # seconds, or immediately on any org activity.
    f = _read_flag()
    if f is None:
        env_on = os.environ.get("ORGTREE_WARM", "1") != "0"
        return env_on, env_on
    if f.get("malformed"):
        return os.environ.get("ORGTREE_WARM", "1") != "0", None
    return bool(f["enabled"]), bool(f["enabled"])


def set_enabled(on: bool) -> None:
    """THE ONE WRITE PATH for the on/off half of the flag — the D-203
    settings toggle and any operator flip both come through here. Preserves
    the per-node exclude list (cache-misses' A/B arms): a bare "0"/"1" write
    would clobber it. The toggle is a user preference and the flag is the
    A/B control and back-out lever — SAME VALUE, on purpose; splitting them
    would let the off-arm measurement and the user's setting disagree."""
    with _FLAG_LOCK:
        # A toggle is a read/modify/write because it must preserve the A/B
        # exclude list. Bypass the 2 s decision cache here: another atomic
        # writer may have replaced the durable list inside that TTL, and
        # writing our cached view back would silently resurrect stale arms.
        _FLAG_CACHE["at"] = 0.0
        f = _read_flag()
        exclude = list(f.get("exclude") or []) \
            if f and not f.get("malformed") else []
        if exclude:
            set_flag(json.dumps({"enabled": bool(on), "exclude": exclude}))
        else:
            set_flag("1" if on else "0")
    poke()


def warm_enabled() -> bool:
    return warm_decision()[0]


def node_excluded(slug: str, nid: str) -> bool:
    f = _read_flag()
    return bool(f and not f.get("malformed")
                and f"{slug}/{nid}" in f["exclude"])


# ── the pool ───────────────────────────────────────────────────────────────
class WarmProc:
    """One parked (or claimed) CLI process. The stdout PUMP thread owns
    proc.stdout for the process's whole life — a turn reads lines through
    `lines_iter()`, never from the pipe directly — because a turn ends at a
    result boundary while the process lives on, and a second turn must be
    able to attach where the first detached."""

    def __init__(self, slug: str, nid: str, proc: subprocess.Popen[str],
                 sid: str, ihash: str, env_id: str,
                 ident_components: dict[str, str] | None = None) -> None:
        self.slug, self.nid = slug, nid
        self.proc = proc
        self.sid = sid
        self.hash = ihash
        self.env_id = env_id
        # D-206 attribution: the four digests that produced `hash`, captured
        # at the same time as the combined hash. Raw prompt/argv/credential
        # values never enter telemetry. A process created before this field
        # existed has no baseline, which is recorded as incomplete rather
        # than guessed.
        self.ident_components = (dict(ident_components)
                                 if ident_components is not None else None)
        self.identity_change: dict[str, Any] | None = None
        self.spawned_at = time.time()
        self.parked_at = time.time()
        self.claimed = False
        # delivery gate (process-cache-2's probe, 2026-08-30): lines reach a
        # turn ONLY between activate() — called right after the turn's first
        # stdin write — and park/detach. Everything else is dropped (newest
        # init excepted). Two measured hazards force this: a straggler result
        # from the PREVIOUS turn arriving after re-claim would read as the new
        # turn's boundary, and any non-system event delivered before the
        # stdin write would satisfy the C1 delivery-confirm for mail the
        # process never read. attach() also swaps in a FRESH queue, so
        # nothing queued at detach can survive into the next claim.
        self.active = False
        self.dead = threading.Event()
        # process-cumulative dollars already BOOKED by earlier turns on this
        # process. `total_cost_usd` accumulates across the process's life, and
        # every booking site in supervisor assumed one process per turn — a
        # warm process serving its second turn would re-book the first turn's
        # spend without this baseline (found in review of supervisor.py:6901
        # before it could ship, 2026-08-30).
        self.cost_base = 0.0
        # …and the same baseline for the result event's cumulative
        # usage.output_tokens (rides the per-turn stats ring)
        self.out_base = 0
        # WHY this process is ending, noted by the turn AT THE DECISION
        # (boundary decline, bg-children drain, limit freeze, timeout kill)
        # and journaled exactly once at true EOF — a serving process that
        # ended without parking used to leave ZERO exit rows, so E3's table
        # could be green while a primary serving death was invisible
        # (process-cache-2's serving-exit probe). `exit_journaled` is the
        # once-guard shared by every journaling path.
        self.exit_reason: str | None = None
        self.exit_journaled = False
        self.lines: "queue.Queue[str | None]" = queue.Queue()
        self.init_line: str | None = None   # latest system/init seen inactive
        self.dropped_inactive = 0           # probe counter (process-cache-2)
        self.err_tail: collections.deque[str] = collections.deque(maxlen=200)
        self._lk = threading.Lock()
        threading.Thread(target=self._pump_out, daemon=True,
                         name=f"warmpump-{slug}-{nid}").start()
        threading.Thread(target=self._pump_err, daemon=True,
                         name=f"warmerr-{slug}-{nid}").start()

    # — pumps —
    def _pump_out(self) -> None:
        try:
            for line in self.proc.stdout:      # pyright: ignore[reportOptionalIterable]
                line = line.rstrip("\n")
                with self._lk:
                    if self.active:
                        self.lines.put(line)
                        continue
                # INACTIVE lines (parked, or claimed but the turn has not yet
                # written stdin) are dropped, except the newest init event,
                # which is replayed to the next claiming turn (it carries the
                # tool/MCP resolution st["init"] wants; init is type=system,
                # which the C1 confirm explicitly refuses as consumption
                # proof). See the `active` field note for why dropping is
                # load-bearing, not tidiness.
                s = line.strip()
                if '"init"' in s:
                    try:
                        ev = json.loads(s)
                        if ev.get("type") == "system" \
                                and ev.get("subtype") == "init":
                            with self._lk:
                                self.init_line = line
                    except ValueError:
                        pass
                else:
                    with self._lk:
                        self.dropped_inactive += 1
        except (OSError, ValueError):
            pass
        self.dead.set()
        with self._lk:
            self.lines.put(None)                # EOF marker for any reader
        _on_proc_exit(self)

    def _pump_err(self) -> None:
        try:
            for line in self.proc.stderr:      # pyright: ignore[reportOptionalIterable]
                raw = line.rstrip("\r\n")
                self.err_tail.append(raw)
                journal_cache_break_lines(
                    self.slug, self.nid, self.sid,
                    getattr(self.proc, "pid", None), "warm-stderr", raw)
        except (OSError, ValueError):
            pass

    # — the turn-side surface —
    def attach(self) -> None:
        with self._lk:
            self.claimed = True
            self.active = False
            # a FRESH queue per claim: nothing queued at a previous detach
            # (a late straggler result, most dangerously) can be replayed
            # into this turn as if it were its own event
            self.lines = queue.Queue()
            # replay the parked-period init so st["init"] is still populated
            if self.init_line is not None:
                self.lines.put(self.init_line)
                self.init_line = None
            if self.dead.is_set():
                self.lines.put(None)     # died before the claim: EOF at once

    def activate(self) -> None:
        """Open the delivery gate — called by the turn immediately after its
        first stdin write+flush. Only lines the pump reads after this point
        reach the turn, so no pre-write event can confirm delivery of a
        payload the process has not read."""
        with self._lk:
            self.active = True

    def lines_iter(self) -> Iterator[str]:
        """Line source for the turn loop. Ends on process EOF (None marker),
        exactly like iterating proc.stdout on the cold path — the idle
        watchdog still bounds a wedged process by killing it, which lands
        here as EOF."""
        q = self.lines            # this claim's queue, pinned
        while True:
            line = q.get()
            if line is None:
                q.put(None)              # stay terminated for any re-reader
                return
            yield line

    def err_text(self) -> str:
        return "\n".join(self.err_tail)

    def alive(self) -> bool:
        return self.proc.poll() is None and not self.dead.is_set()


_pool: dict[tuple[str, str], WarmProc] = {}
# warm-origin processes CURRENTLY SERVING a turn (claimed out of _pool).
# Tracked so the pool snapshot sees peak memory — parked-only counting
# structurally hid every serving process, which corrupts the ceiling figure
# the user asked for (cache-misses contract, coordinator-backed). ONLY
# telemetry and exit bookkeeping read this: kill_node/kill_org deliberately
# do not — a mid-turn process is never disturbed, tracked or not.
_serving: dict[tuple[str, str], WarmProc] = {}
_pool_lock = threading.RLock()
_spawn_gate = threading.Semaphore(SPAWN_PACE)
_poke = threading.Event()
_started = False
# test seam (process-cache-2's contract): patch this to fake the CLI process
# factory without touching subprocess itself
_POPEN = subprocess.Popen


# ── telemetry ──────────────────────────────────────────────────────────────
_J_LOCK = threading.Lock()
CACHE_BREAK_MARKER = "[PROMPT CACHE BREAK]"
CACHE_BREAK_LINE_MAX = 4096


def _journal(kind: str, **fields: Any) -> None:
    rec = {"kind": kind,
           "at": time.strftime("%Y-%m-%dT%H:%M:%S.", time.gmtime())
                 + f"{int(time.time() * 1000) % 1000:03d}Z",
           **fields}
    try:
        d = os.path.join(store.DATA_ROOT, "journals")
        os.makedirs(d, exist_ok=True)
        with _J_LOCK, open(os.path.join(d, "warm.jsonl"), "a",
                           encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass                     # telemetry must never cost a turn


def journal_cache_break_lines(slug: str, nid: str, sid: str,
                              pid: int | None, source: str,
                              text: str) -> None:
    """Persist only the CLI's cache-break sentinel from stderr.

    General stderr may contain sensitive material and stays in the existing
    private tail. The raw sentinel line is the diagnoser's evidence, so keep
    it verbatim apart from line terminators and a deterministic 4096-character
    cap. This helper is shared by BOTH stderr owners: WarmProc._pump_err and
    read_cold_stderr. Missing either owner removes exactly the population this
    instrument is meant to explain. `_journal`'s `at` is COLLECTION time, not
    an API request timestamp; consumers join by session/order plus the raw
    line's call/read/create tuple (the warning carries no requestId).
    """
    for raw in text.splitlines() or [text.rstrip("\r\n")]:
        if CACHE_BREAK_MARKER not in raw:
            continue
        _journal("cache-break", slug=slug, nid=nid, session_id=sid,
                 pid=pid, source=source, line=raw[:CACHE_BREAK_LINE_MAX],
                 raw_length=len(raw),
                 truncated=len(raw) > CACHE_BREAK_LINE_MAX)


def read_cold_stderr(proc: subprocess.Popen[str], slug: str, nid: str,
                     sid: str) -> str:
    """The non-pooled stderr owner, with the same cache-break observation as
    the warm pump. Kept as one helper so a cold success cannot read and then
    silently discard the very warning that explains its cache miss."""
    err = proc.stderr.read()     # pyright: ignore[reportOptionalMemberAccess]
    journal_cache_break_lines(slug, nid, sid, getattr(proc, "pid", None),
                              "cold-stderr", err)
    return err


def journal_admit(slug: str, nid: str, sid: str, served: str, reason: str,
                  ihash: str, warm_age_s: float | None, spawn_ms: int,
                  warm_label: bool | None, slot_wait_s: float = 0.0) -> None:
    """One line per turn ADMISSION, written before the turn runs (a crash
    mid-turn cannot lose it). `session_id` is cache-misses' join key against
    the CLI transcript.

    `warm_label` is THE FLAG OBSERVATION FROM THE DECISION THAT ADMITTED
    THIS TURN, threaded through by the caller — never re-read here. The flag
    can flip between the decision and this write, and an admit row labelled
    with an arm the turn was not served under is silent misattribution in
    the A/B (process-cache-2's alternating-flag probe measured exactly
    that). None = the flag file was malformed at decision time: the arm is
    UNKNOWN and recorded as such, never guessed.

    `slot_wait_s` is how long this admission waited to acquire the
    machine-wide `_turn_slots` seat (0.0 for a boundary-feed, which rides a
    seat the turn already holds rather than acquiring a new one). A stuck-mail
    incident (user report 2026-08-30) traced to a window this journal could
    not explain either way — a slot wait leaves no OTHER trace anywhere, so
    without this field the next occurrence would be just as unexplainable.

    ⚠ THERE IS DELIBERATELY NO `handshake_ms` FIELD. One used to be written
    here as the literal `0`, and cache-misses measured it vacuous
    (2026-08-30): 0 in all 46 admit rows, cold spawns included. What makes
    that a finding rather than a guess is the control — `spawn_ms` in the
    SAME rows varies 0–483ms, so the journal does record varying values and
    that field specifically was dead.

    It is REMOVED rather than wired up, because there is no interval here to
    measure. This pool by explicit user ruling (module docstring) NEVER WAITS
    FOR THE MCP HANDSHAKE anywhere, so nothing at this seam observes the
    handshake starting or finishing; any number written here would be
    invented. Timing it means watching the CLI's own MCP readiness output,
    which is a feature, not a field.

    A MISSING FIELD IS HONEST; A PERMANENTLY-ZERO ONE LIES — and this one sat
    in the journal this whole effort steers by, where `handshake_ms=0` on a
    cold spawn reads as evidence the handshake was free, which is the
    opposite of what the audit found. Do not re-add it without a proof that
    it can be non-zero (coordinator-ruled 2026-08-30)."""
    _journal("admit", slug=slug, nid=nid, session_id=sid, served=served,
             reason=reason, ident_hash=ihash,
             warm_age_s=round(warm_age_s, 1) if warm_age_s is not None else None,
             spawn_ms=spawn_ms, warm_enabled=warm_label,
             slot_wait_s=round(slot_wait_s, 1))


def _journal_proc(event: str, slug: str, nid: str, reason: str,
                  ihash: str = "", elapsed_ms: int = 0,
                  session_id: str | None = None,
                  pid: int | None = None,
                  identity_change: dict[str, Any] | None = None) -> None:
    try:
        from . import supervisor as sup             # noqa: PLC0415
        with sup._state_lock:
            qd = len(sup._state.get((slug, nid), {}).get("queue") or [])
    except Exception:                               # noqa: BLE001
        qd = 0
    rec: dict[str, Any] = dict(slug=slug, nid=nid, event=event, reason=reason,
                               ident_hash=ihash, queue_depth=qd,
                               elapsed_ms=elapsed_ms)
    if session_id is not None:
        rec["session_id"] = session_id
    if pid is not None:
        rec["pid"] = pid
    if identity_change is not None:
        rec.update(identity_change)
    if event == "exit":
        # every death names its class, so the closed-death-list invariant is
        # checkable from the journal alone
        rec["reason_class"] = _classify_kill(slug, nid, reason)
    _journal("proc", **rec)


# ── identity hash ──────────────────────────────────────────────────────────
def _argv_normalized(cmd: list[str]) -> list[str]:
    """The spawn argv with ONE normalization: the trailing session flag NAME.
    `_build_cmd` emits `--session-id <sid>` before a transcript exists and
    `--resume <sid>` after — the first real turn flips it, and treating that
    flip as an identity change would respawn every agent right after its
    first turn for no reason. The SID ITSELF STAYS IN THE HASH: a cheap
    compact mints a new session, and a parked process holding the old one
    must be invalidated."""
    out = list(cmd)
    for i, a in enumerate(out):
        if a in ("--session-id", "--resume"):
            out[i] = "<session>"
    return out


_STARTUP_IMPORT_RE = re.compile(r"(?<![\w@])@([^\s`\"'<>]+)")
_RULE_PATHS_RE = re.compile(r"(?m)^paths\s*:")


def _memory_prefix(data: bytes) -> bytes:
    """The exact documented auto-memory startup ceiling: first 25 KiB or
    first 200 lines, whichever comes first."""
    data = data[:25 * 1024]
    return b"".join(data.splitlines(keepends=True)[:200])


def _startup_rule(data: bytes) -> bool:
    """A path-scoped rule is lazy, not a session-start input."""
    text = data.decode("utf-8", "replace")
    if not text.startswith("---"):
        return True
    end = text.find("\n---", 3)
    frontmatter = text[3:end if end >= 0 else len(text)]
    return _RULE_PATHS_RE.search(frontmatter) is None


def native_startup_context_digest(org: Any, nid: str) -> str:
    """Digest Claude's file-borne, once-per-session instruction inputs.

    Claude Code loads these before turn 1 and holds them in the process. A
    warm process therefore becomes correctness-stale when one changes unless
    the file participates in the identity hash. This deliberately excludes
    global skills: the pinned CLI watches skill directories live, so hashing
    them would manufacture respawns rather than prevent stale instructions.

    The manifest contains paths and content hashes, never raw instruction
    text. CLAUDE.md imports are followed to the CLI's documented five-hop
    ceiling; auto memory is hashed only through the prefix the CLI loads.
    """
    from . import supervisor as sup                 # noqa: PLC0415

    cwd = os.path.abspath(sup.scratch_dir(org.d["slug"], nid))
    home = os.path.abspath(os.path.expanduser("~"))
    manifest: dict[str, str] = {}
    seen: set[str] = set()

    def add(path: str, depth: int = 0, *, memory: bool = False,
            rule: bool = False) -> None:
        path = os.path.abspath(os.path.expanduser(path))
        key = os.path.normcase(os.path.realpath(path))
        if key in seen:
            return
        try:
            with open(path, "rb") as f:
                data = f.read()
        except (OSError, ValueError):
            return
        if rule and not _startup_rule(data):
            return
        seen.add(key)
        if memory:
            data = _memory_prefix(data)
        label = os.path.normcase(path)
        manifest[label] = hashlib.sha256(data).hexdigest()
        if depth >= 5:
            return
        text = data.decode("utf-8", "replace")
        for match in _STARTUP_IMPORT_RE.finditer(text):
            token = match.group(1).rstrip(".,;:!?)]}")
            if not token:
                continue
            imported = (os.path.expanduser(token) if token.startswith("~")
                        else token if os.path.isabs(token)
                        else os.path.join(os.path.dirname(path), token))
            add(imported, depth + 1)

    # Managed policy, then user instructions.
    if os.name == "nt":
        add(os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                         "ClaudeCode", "CLAUDE.md"))
    else:
        add("/etc/claude-code/CLAUDE.md")
        add("/Library/Application Support/ClaudeCode/CLAUDE.md")
    add(os.path.join(home, ".claude", "CLAUDE.md"))

    # Project instructions: root -> cwd, then the project-local .claude
    # form. The cwd is outside a git tree for normal orgtree seats, so it is
    # the project root; walking parents is still required by Claude Code.
    chain: list[str] = []
    at = cwd
    while True:
        chain.append(at)
        parent = os.path.dirname(at)
        if parent == at:
            break
        at = parent
    for directory in reversed(chain):
        add(os.path.join(directory, "CLAUDE.md"))
        add(os.path.join(directory, "CLAUDE.local.md"))
    add(os.path.join(cwd, ".claude", "CLAUDE.md"))

    # Unscoped rules load at session start; path-scoped rules load lazily when
    # a matching file is read and therefore must not dirty an idle process.
    for rules_root in (os.path.join(home, ".claude", "rules"),
                       os.path.join(cwd, ".claude", "rules")):
        try:
            for root, dirs, files in os.walk(rules_root):
                dirs.sort()
                for name in sorted(files):
                    if name.endswith(".md"):
                        add(os.path.join(root, name), rule=True)
        except OSError:
            pass

    # Auto memory is another documented session-start input. Topic files are
    # lazy; only MEMORY.md's bounded prefix belongs here.
    memory = os.path.join(home, ".claude", "projects",
                          sup._cli_project_dir(cwd), "memory", "MEMORY.md")
    add(memory, memory=True)

    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False).encode("utf-8", "replace")
    return hashlib.sha256(encoded).hexdigest()


def _part(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:12]


IDENTITY_COMPONENTS = ("prompt", "argv", "cred", "envov")


def identity_snapshot(org: Any, nid: str, *,
                      cmd: list[str] | None = None,
                      env: dict[str, str] | None = None,
                      overrides: dict[str, str] | None = None,
                      ) -> tuple[str, dict[str, str]]:
    """One combined identity hash plus independently verifiable components.

    Optional cmd/env/overrides are the values an actual spawn is about to
    receive. Supplying them is important: resolving the credential a second
    time can choose another account and falsely dirty (or falsely admit) a
    parked process. Raw inputs never leave this function; journals receive
    only the short SHA-256 component digests.
    """
    from . import supervisor as sup                 # noqa: PLC0415
    prompt = sup.identity_prompt(org, nid)
    if cmd is None:
        cmd = sup._build_cmd(org, nid, write_ident=False)
    if overrides is None:
        overrides = sup.env_overrides(org.d["slug"], nid)
    if env is None:
        env = sup.spawn_env(org,
                            tier=str(org.node(nid).get("model") or ""),
                            nid=nid)
    native = native_startup_context_digest(org, nid)
    raw = {
        # Keep the fixed four-component vocabulary cache-misses approved:
        # native instructions are prompt input, not a fifth identity class.
        "prompt": (prompt.encode("utf-8", "replace")
                   + b"\x00native-startup\x00" + native.encode("ascii")),
        "argv": json.dumps(_argv_normalized(cmd), ensure_ascii=False)
                    .encode("utf-8", "replace"),
        "cred": sup.identity_in_env(env).encode("utf-8", "replace"),
        "envov": json.dumps(overrides, sort_keys=True, ensure_ascii=False)
                     .encode("utf-8", "replace"),
    }
    h = hashlib.sha256()
    for i, name in enumerate(IDENTITY_COMPONENTS):
        if i:
            h.update(b"\x00")
        h.update(raw[name])
    return h.hexdigest()[:32], {name: _part(raw[name])
                                for name in IDENTITY_COMPONENTS}


def ident_hash(org: Any, nid: str) -> str:
    """The invalidation hash. Pure — writes nothing."""
    return identity_snapshot(org, nid)[0]


def ident_parts(org: Any, nid: str) -> dict[str, str]:
    """The four short component digests, mainly for diagnostics/tests."""
    return identity_snapshot(org, nid)[1]


def identity_change_fields(previous_hash: str,
                           previous_components: dict[str, str] | None,
                           next_hash: str,
                           next_components: dict[str, str] | None,
                           ) -> dict[str, Any]:
    """A falsifiable attribution record: consumer can recompute the changed
    names from the two digest maps instead of trusting the producer's label."""
    prev_ok = (isinstance(previous_components, dict)
               and all(isinstance(previous_components.get(k), str)
                       for k in IDENTITY_COMPONENTS))
    next_ok = (isinstance(next_components, dict)
               and all(isinstance(next_components.get(k), str)
                       for k in IDENTITY_COMPONENTS))
    complete = bool(prev_ok and next_ok)
    previous = ({k: previous_components[k] for k in IDENTITY_COMPONENTS}
                if prev_ok and previous_components is not None else None)
    nxt = ({k: next_components[k] for k in IDENTITY_COMPONENTS}
           if next_ok and next_components is not None else None)
    changed = ([k for k in IDENTITY_COMPONENTS
                if previous[k] != nxt[k]]
               if complete and previous is not None and nxt is not None
               else None)
    return {
        "previous_ident_hash": previous_hash,
        "next_ident_hash": next_hash,
        "previous_components": previous,
        "next_components": nxt,
        "changed_inputs": changed,
        "attribution_complete": complete,
    }


def _record_identity_change(wp: WarmProc, next_hash: str,
                            next_components: dict[str, str] | None,
                            ) -> dict[str, Any]:
    fields = identity_change_fields(
        wp.hash, wp.ident_components, next_hash, next_components)
    with wp._lk:
        wp.identity_change = fields
    return fields


def eligible(org: Any, nid: str) -> tuple[bool, str]:
    """May this node hold a warm process at all? Everything outside this set
    keeps today's spawn-per-turn behaviour, which is also the universal
    fallback. v1 excludes the sandbox (its spawn is a docker exec whose
    parking is untested) and the provider lanes (codex/gemini run their own
    processes) and preserving oracles (each consult is a --fork-session)."""
    from . import supervisor as sup                 # noqa: PLC0415
    from . import providers                         # noqa: PLC0415
    n = org.nodes.get(nid)
    if not n or n.get("state") != "live":
        return False, "not-live"
    if node_excluded(org.d["slug"], nid):
        return False, "excluded-by-flag"
    if sup.sbx.is_sandboxed(org):
        return False, "sandboxed"
    model = str(n.get("model") or "")
    if model in providers.CODEX_TIERS or model in providers.GEMINI_TIERS:
        return False, "provider-lane"
    if n.get("bearer_state") == "preserving":
        return False, "preserving-oracle"
    return True, ""


def boundary_check(slug: str, nid: str,
                   want_hash: str | None,
                   wp: WarmProc | None = None,
                   ) -> tuple[bool, bool | None, str]:
    """The result-boundary decision, with ONE flag read shared between the
    behaviour and the label → (may this process keep serving, the label for
    any admit row this boundary writes, WHY when it may not — the exit
    reason the turn notes on the process so its death row is classified).
    False whenever the switch is off, the node is excluded, eligibility
    lapsed, or the identity hash moved — a queued message is 'the next
    turn' and never rides any of those."""
    on, label = warm_decision()
    if not on:
        return False, label, "disabled"
    if want_hash is None:
        return False, label, "stdin-closed"
    if node_excluded(slug, nid):
        return False, label, "excluded-by-flag"
    try:
        org = store.load_org(slug)
        ok, why = eligible(org, nid)
        if not ok:
            return False, label, why or "not-eligible"
        next_hash, next_components = identity_snapshot(org, nid)
        if next_hash == want_hash:
            return True, label, ""
        if wp is not None:
            _record_identity_change(wp, next_hash, next_components)
        return False, label, "identity-changed"
    except Exception:                               # noqa: BLE001
        return False, label, "stdin-closed"


def current_hash(slug: str, nid: str) -> str | None:
    """Fresh-off-disk hash for boundary/park-time revalidation; None = 'this
    seat has no valid warm identity RIGHT NOW', which callers must treat as
    'do not feed, do not park'. THE KILL SWITCH IS AUTHORITATIVE HERE TOO
    (process-cache-2 finding, coordinator-ruled): flipping warm.flag off
    mid-turn must stop the very next queued boundary message from riding the
    warm process, or the A/B's off arm is partly on and the back-out lever
    only half works."""
    if not warm_enabled() or node_excluded(slug, nid):
        return None
    try:
        org = store.load_org(slug)
        ok, _why = eligible(org, nid)
        if not ok:
            return None
        return ident_hash(org, nid)
    except Exception:                               # noqa: BLE001
        return None


# ── state mirror for the UI (styling contract: proc_warm boolean) ─────────
def _set_proc_warm(slug: str, nid: str, val: bool) -> None:
    try:
        from . import supervisor as sup             # noqa: PLC0415
        ent = sup.state(slug, nid)      # allocates; takes _state_lock itself
        with sup._state_lock:
            ent["proc_warm"] = val
        sup.notify(slug, nid, "proc_warm" if val else "proc_cold")
    except Exception:                               # noqa: BLE001
        pass


def is_warm(slug: str, nid: str) -> bool:
    with _pool_lock:
        wp = _pool.get((slug, nid))
        return bool(wp and not wp.claimed and wp.alive())


# ── spawn / kill ───────────────────────────────────────────────────────────
# THE CLOSED DEATH LIST, made structural (coordinator ruling 2026-08-30,
# after four separate probes found teardowns outside it), reconciled against
# the user's ruling which names exactly THREE ways a process may end:
# retirement, an explicit system-prompt change, and orgtree shutdown.
#
# How this table maps onto those three — the authoritative statement lives
# in the D-201 register entry; this is its enforcement:
# · retirement, prompt-change — the user's first two, verbatim.
# · SHUTDOWN IS DELIBERATELY NOT A ROW. It is enforced by the OS job object
#   (`_leash` at spawn: KILL_ON_JOB_CLOSE), not by any code path here —
#   which is the only shape that cannot be skipped by a hard kill of the
#   backend. No Python runs when orgtree dies, so a vocabulary row would be
#   a row nothing could ever consult. Pinned by the suite's leash check.
# · kill-switch — an ADDITION to the user's list, coordinator-ordered: the
#   A/B measurement arm and the production back-out lever. A lever that
#   cannot kill is not a lever.
# · duplicate-resolution — an engineering artifact, not a seat death: the
#   hire-kickoff/keeper race can briefly make TWO processes for one seat
#   (the alternative was holding a global lock across Popen on the turn
#   path); the redundant one is killed and THE SEAT KEEPS WARM COVERAGE
#   throughout. Journaled every time, so "rare" stays checkable.
# · observed-death — NOT A KILL AT ALL: bookkeeping for a process found
#   already dead (crash). It is in this table so every pool EXIT carries a
#   class, but it grants nobody permission to end anything.
#
# Every deliberate kill must carry a reason from this table; a reason it
# does not know is journaled as UNLISTED and printed loudly, because an
# unenumerated teardown is exactly the defect family this exists to catch.
KILL_REASON_CLASS = {
    "retired": "retirement", "org-deleted": "retirement",
    "not-live": "retirement",
    "identity-changed": "prompt-change",
    "renamed": "prompt-change",          # a rename changes nid → prompt+argv
    "provider-lane": "prompt-change",    # eligibility flips are model/scope
    "sandboxed": "prompt-change",        # changes, i.e. identity changes
    "preserving-oracle": "prompt-change",
    "disabled": "kill-switch",           # the coordinator-ordered A/B and
    "excluded-by-flag": "kill-switch",   # back-out lever, sanctioned
    "superseded": "duplicate-resolution",  # the seat KEEPS a warm process
    "crash": "observed-death",           # the process died; we only noticed
    "not-eligible": "prompt-change",     # eligibility lapse = scope change
    # a SERVING process that could not park and drained to exit — today's
    # turn machinery ending a process the old way, journaled so the death
    # is visible, distinct from the pool's own deliberate kills:
    "turn-timeout": "turn-machinery",    # the idle/budget watchdog killed it
    "limit-frozen": "turn-machinery",    # usage limit froze the seat
    "background-children": "turn-machinery",  # lives on till bg agents land
    "stdin-closed": "turn-machinery",    # generic non-park drain-to-exit
    "suite-teardown": "test",
}


def _journal_exit_once(wp: WarmProc, reason: str | None = None) -> None:
    """EXACTLY ONE classified exit row per warm-origin process, whoever gets
    there first — the deliberate-kill paths write it at the kill, the pump's
    EOF writes it for every non-park end the turn only annotated (or for a
    true crash nobody annotated, the observed-death fallback)."""
    with wp._lk:
        if wp.exit_journaled:
            return
        wp.exit_journaled = True
        r = reason or wp.exit_reason or "crash"
        change = (dict(wp.identity_change)
                  if r == "identity-changed" and wp.identity_change else None)
    _journal_proc("exit", wp.slug, wp.nid, r, wp.hash,
                  session_id=wp.sid, pid=getattr(wp.proc, "pid", None),
                  identity_change=change)


def _classify_kill(slug: str, nid: str, reason: str) -> str:
    cls = KILL_REASON_CLASS.get(reason)
    if cls is None:
        print(f"[orgtree] warmpool ⚠ UNLISTED KILL REASON {reason!r} for "
              f"{slug}/{nid} — a warm process is being torn down outside "
              f"the closed death list; this is a defect to report, not a "
              f"style issue")
        return "UNLISTED"
    return cls


def _kill_proc(wp: WarmProc) -> None:
    """Tree kill: on Windows proc.kill() alone leaves the MCP servers alive
    (see _wd_kill_tree's note) — the CLI's children must die with it."""
    try:
        from . import supervisor as sup             # noqa: PLC0415
        sup._wd_kill_tree(wp.proc)
    except Exception:                               # noqa: BLE001
        try:
            wp.proc.kill()
        except Exception:                           # noqa: BLE001
            pass


def _on_proc_exit(wp: WarmProc) -> None:
    """Pump saw EOF: the process ended (kill, crash, or backend teardown).
    Drop it from whichever registry holds it. A parked death journals here;
    a SERVING death only leaves the registry — its turn owns the aftermath
    and the story (park refusal, error path, its own journal lines)."""
    was_tracked = False
    with _pool_lock:
        if _serving.get((wp.slug, wp.nid)) is wp:
            del _serving[(wp.slug, wp.nid)]
            was_tracked = True
        cur = _pool.get((wp.slug, wp.nid))
        if cur is wp and not wp.claimed:
            del _pool[(wp.slug, wp.nid)]
            was_tracked = True
            _set_proc_warm(wp.slug, wp.nid, False)
    # EOF is the one observation point EVERY warm-origin process passes —
    # the journal-once guard makes this the backstop, not a duplicate: a
    # deliberate kill already wrote its row and this no-ops; a serving
    # process that drained to exit carries the reason its turn noted; a
    # true crash falls back to observed-death. (A process that already
    # left both registries — claimed out and discarded — journaled at the
    # discard; the guard covers it too.)
    if was_tracked or wp.claimed:
        _journal_exit_once(wp)


def _spawn_for(org: Any, nid: str, why: str) -> WarmProc | None:
    """Spawn and park one CLI process, exactly the shape a turn spawn uses
    (production argv, leashed to the backend's job object). NO message is
    written and NO handshake is awaited — a parked process makes no API call
    (parkprobe.py: 2/2 survived, 0 transcripts, 0 tokens)."""
    from . import supervisor as sup                 # noqa: PLC0415
    slug = org.d["slug"]
    t0 = time.time()
    try:
        cmd = sup._build_cmd(org, nid)
        env = sup.spawn_env(org, tier=str(org.node(nid).get("model") or ""),
                            nid=nid)
        ov = sup.env_overrides(slug, nid)
        ih, components = identity_snapshot(
            org, nid, cmd=cmd, env=env, overrides=ov)
        env_id = sup.identity_in_env(env)
        env["ORGTREE_ORG"], env["ORGTREE_NODE"] = slug, nid
        env["ORGTREE_PORT"] = os.environ.get("ORGTREE_PORT", "7360")
        env["PYTHONPATH"] = (sup.BACKEND_DIR + os.pathsep
                             + env.get("PYTHONPATH", ""))
        _journal_proc("respawn-start", slug, nid, why, ih)
        proc = _POPEN(
            cmd, cwd=sup.scratch_dir(slug, nid), env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
            errors="replace")
        try:
            sup._leash(proc)
            wp = WarmProc(slug, nid, proc,
                          org.node(nid)["session_id"], ih, env_id,
                          components)
        except Exception:
            # setup died AFTER the child existed: reap it or every keeper
            # retry leaks a CLI+MCP tree while turns stay correct — the
            # silent-fallback shape (process-cache-2's spawn-cleanup probe)
            try:
                sup._wd_kill_tree(proc)
            except Exception:                       # noqa: BLE001
                try:
                    proc.kill()
                except Exception:                   # noqa: BLE001
                    pass
            raise
        _journal_proc("respawn-done", slug, nid, why, ih,
                      elapsed_ms=int((time.time() - t0) * 1000))
        return wp
    except Exception as e:                          # noqa: BLE001
        # a failed pre-warm is a non-event for the agent: the next turn just
        # spawns cold, exactly like today. Log it — silently degrading to
        # 100% cold with every test green is this feature's failure mode.
        print(f"[orgtree] warmpool: pre-warm of {slug}/{nid} failed "
              f"({type(e).__name__}: {e}) — the agent falls back to "
              f"spawn-per-turn and notices nothing")
        return None


def kill_node(slug: str, nid: str, reason: str) -> None:
    """Immediate teardown for a node's warm process (retire, dissolve,
    rename — a parked process's cwd would block the scratch move). A CLAIMED
    process is mid-turn and is NOT touched: the turn owns it, and the keeper
    handles the aftermath at turn end."""
    with _pool_lock:
        wp = _pool.get((slug, nid))
        if wp is None or wp.claimed:
            return
        del _pool[(slug, nid)]
    _kill_proc(wp)
    _set_proc_warm(slug, nid, False)
    _journal_exit_once(wp, reason)


def kill_org(slug: str, reason: str) -> None:
    with _pool_lock:
        keys = [k for k in _pool if k[0] == slug]
    for _s, nid in keys:
        kill_node(slug, nid, reason)


# ── the turn-side API ──────────────────────────────────────────────────────
_CLAIM_SNAPSHOT = threading.local()


def claim_snapshot(slug: str, nid: str, want_hash: str,
                   want_components: dict[str, str] | None,
                   ) -> tuple[WarmProc | None, str]:
    """Carry the turn's exact component snapshot into the stable 3-argument
    claim seam. The thread local avoids cross-turn races and deliberately
    calls `claim` by name so the suite's death-between-claim-and-write mutant
    can still wrap that seam without learning a new signature."""
    _CLAIM_SNAPSHOT.value = (slug, nid, want_hash, want_components)
    try:
        return claim(slug, nid, want_hash)
    finally:
        _CLAIM_SNAPSHOT.value = None


def claim(slug: str, nid: str,
          want_hash: str) -> tuple[WarmProc | None, str]:
    """Hand the pooled process to a starting turn IFF it is alive and holds
    the current identity hash. A mismatch is killed on the spot (stale prompt
    = correctness hazard, never served) and the caller spawns cold. The
    second return is the admit-journal reason when no process is served.

    A CLAIMED process moves from the pool to the SERVING registry — the turn
    owns it, and `park_back` is the only way back in. The pool therefore
    holds parked processes only, which is also exactly what `proc_warm`
    means; the serving registry exists so telemetry still sees the process.

    THE CALLER OWNS THE FLAG DECISION: this function does not consult
    warm_enabled() — the spawn site read the flag exactly once and only
    calls here when that read said on. A second read here could disagree
    with the label the admit row carries (cache-misses' misattribution
    hazard)."""
    snap = getattr(_CLAIM_SNAPSHOT, "value", None)
    want_components = (snap[3]
                       if snap and snap[:3] == (slug, nid, want_hash) else None)
    with _pool_lock:
        wp = _pool.get((slug, nid))
        if wp is None:
            return None, "no-process"
        was_alive = wp.alive()          # decided BEFORE any kill, or the
        del _pool[(slug, nid)]          # journal always says "crash"
        if was_alive and wp.hash == want_hash:
            wp.attach()
            _serving[(slug, nid)] = wp
            _set_proc_warm(slug, nid, False)   # claimed = no longer parked
            return wp, "warm-hit"
    # (outside the lock) the mismatched/dead one dies now
    if was_alive:
        _record_identity_change(wp, want_hash, want_components)
    _kill_proc(wp)
    _set_proc_warm(slug, nid, False)
    _journal_exit_once(wp, "identity-changed" if was_alive else "crash")
    return None, ("identity-changed" if was_alive else "crashed")


def park_back(wp: WarmProc, cost_base: float, out_base: int = 0) -> bool:
    """A turn finished on this process and nothing dirtied it: return it to
    the pool. If the pool already holds a DIFFERENT process for the seat (the
    hire-kickoff race can double-spawn), prefer THIS one — it holds the
    conversation hot and its next request is a pure cache extension — and
    kill the other."""
    if not wp.alive() or not warm_enabled():
        return False
    other: WarmProc | None = None
    with _pool_lock:
        if _serving.get((wp.slug, wp.nid)) is wp:
            del _serving[(wp.slug, wp.nid)]
        other = _pool.get((wp.slug, wp.nid))
        if other is wp:
            other = None
        with wp._lk:
            wp.active = False       # close the delivery gate BEFORE parking:
            wp.claimed = False      # post-park stragglers must never queue
        _pool[(wp.slug, wp.nid)] = wp
        wp.parked_at = time.time()
        # cumulative counters only grow; max() so a turn that saw no cost
        # event cannot roll an earlier baseline back
        wp.cost_base = max(wp.cost_base, cost_base)
        wp.out_base = max(wp.out_base, out_base)
    if other is not None:
        _kill_proc(other)
        _journal_exit_once(other, "superseded")
    _set_proc_warm(wp.slug, wp.nid, True)
    _journal_proc("park", wp.slug, wp.nid, "turn-end", wp.hash)
    return True


def discard(wp: WarmProc, reason: str) -> None:
    """A turn ends on a process that must not be parked (dirtied mid-turn,
    background children live, limit hit). Kill it — today's teardown."""
    with _pool_lock:
        if _pool.get((wp.slug, wp.nid)) is wp:
            del _pool[(wp.slug, wp.nid)]
        if _serving.get((wp.slug, wp.nid)) is wp:
            del _serving[(wp.slug, wp.nid)]
    _kill_proc(wp)
    _set_proc_warm(wp.slug, wp.nid, False)
    _journal_exit_once(wp, reason)


def poke() -> None:
    """Wake the keeper now — called from store.save_hooks (any org change may
    have dirtied a prompt) and from hire/retire/turn-end sites. Idempotent
    and cheap; the keeper does the hashing."""
    _poke.set()


# ── the keeper ─────────────────────────────────────────────────────────────
def _busy(slug: str, nid: str) -> bool:
    """A seat is skipped only while a turn actually RUNS on it (its park owns
    the pool slot then). `waiting` and a non-empty queue are deliberately NOT
    grounds to skip (process-cache-2 finding, coordinator-ruled): a seat
    blocked on a turn slot benefits most from a spawn racing its wait, and
    the double-spawn that race can produce is resolved at park time
    (`park_back` keeps the just-ran process and kills the other)."""
    from . import supervisor as sup                 # noqa: PLC0415
    with sup._state_lock:
        ent = sup._state.get((slug, nid)) or {}
        return bool(ent.get("busy"))


def _keeper_pass() -> None:
    from . import supervisor as sup                 # noqa: PLC0415
    if not warm_enabled():
        # the OFF arm must be clean for the A/B: parked processes are torn
        # down, not merely unused, so "warm off" measures today's behaviour
        with _pool_lock:
            keys = list(_pool)
        for slug, nid in keys:
            kill_node(slug, nid, "disabled")
        return
    orgs = store.list_orgs()
    known = {o["slug"] for o in orgs}
    # a DELETED org never appears in the loop below — its parked processes
    # would otherwise be orphans no pass ever visits
    with _pool_lock:
        gone = [k for k in _pool if k[0] not in known]
    for slug, nid in gone:
        kill_node(slug, nid, "org-deleted")
    for o in orgs:
        slug = o["slug"]
        try:
            org = store.load_org(slug)
        except Exception:                           # noqa: BLE001
            continue
        live = {k for k, n in org.nodes.items() if n.get("state") == "live"}
        # reap processes whose seat is gone or no longer eligible — retire
        # and dissolve do not touch process state anywhere else (measured
        # gap: supervisor leaves st["proc"] and _state intact on archive)
        with _pool_lock:
            held = [k for k in _pool if k[0] == slug]
        for _s, nid in held:
            if nid not in live:
                kill_node(slug, nid, "retired")
                continue
            ok, why = eligible(org, nid)
            if not ok:
                kill_node(slug, nid, why)
        for nid in sorted(live):
            ok, _why = eligible(org, nid)
            if not ok or _busy(slug, nid):
                continue          # a busy seat's lifecycle belongs to its turn
            with _pool_lock:
                wp = _pool.get((slug, nid))
                if wp is not None and wp.claimed:
                    continue
            if wp is not None and not wp.alive():
                with _pool_lock:
                    if _pool.get((slug, nid)) is wp:
                        del _pool[(slug, nid)]
                _set_proc_warm(slug, nid, False)
                # Keep the same session/pid join contract as every other
                # exit owner, and share the once-only guard with the EOF pump.
                _journal_exit_once(wp, "crash")
                wp = None
            try:
                h, next_components = identity_snapshot(org, nid)
            except Exception:                       # noqa: BLE001
                continue
            if wp is not None and wp.hash == h:
                continue                             # warm and current
            if wp is not None:
                # THE SYSTEM PROMPT CHANGED — the only respawn reason there
                # is. Immediate and eager (user ruling): kill and re-warm
                # now, in the background, so the new process is ready before
                # any turn arrives.
                change = _record_identity_change(wp, h, next_components)
                _journal_proc("dirty", slug, nid, "identity-changed", h,
                              session_id=wp.sid,
                              pid=getattr(wp.proc, "pid", None),
                              identity_change=change)
                kill_node(slug, nid, "identity-changed")
            with _spawn_gate:
                if _busy(slug, nid):                 # a turn started meanwhile
                    continue
                nwp = _spawn_for(org, nid, "pre-warm" if wp is None
                                 else "identity-changed")
            if nwp is None:
                continue
            with _pool_lock:
                if _pool.get((slug, nid)) is None and not _busy(slug, nid):
                    _pool[(slug, nid)] = nwp
                    nwp.claimed = False
                else:
                    # raced a turn (its park wins) or a parallel spawn: kill
                    # OUR redundant spawn — the seat keeps warm coverage
                    # through the winner. Journaled like every other exit;
                    # an unjournaled kill is an exit with no tripwire on it.
                    _kill_proc(nwp)
                    _journal_exit_once(nwp, "superseded")
                    nwp = None
            if nwp is not None:
                _set_proc_warm(slug, nid, True)


def _pool_snapshot() -> None:
    """The line that decides whether warming actually holds. `eligible` is
    counted from the POPULATION THAT SHOULD BE WARM (live + eligible, straight
    off the org docs), never from the pool itself — `eligible=len(entries)`
    was refused in review as an instrument mathematically incapable of
    reporting a failed pre-warm. warm_count < eligible IS the underwarming
    signal. RSS sums each CLI's WHOLE process tree: the MCP servers and any
    wrapper are the part of the memory question nobody counts."""
    with _pool_lock:
        parked = [(wp.proc.pid, wp.slug, wp.nid) for wp in _pool.values()
                  if wp.alive()]
        serving = [(wp.proc.pid, wp.slug, wp.nid)
                   for wp in _serving.values() if wp.alive()]
    entries = parked + serving
    elig_total = 0
    for o in store.list_orgs():
        try:
            org = store.load_org(o["slug"])
        except Exception:                            # noqa: BLE001
            continue
        for nid, n in org.nodes.items():
            if n.get("state") == "live" and eligible(org, nid)[0]:
                elig_total += 1
    rss_total = 0
    free = 0
    try:
        import psutil                                # noqa: PLC0415
        for pid, _s, _n in entries:
            try:
                p = psutil.Process(pid)
                rss_total += p.memory_info().rss
                for c in p.children(recursive=True):
                    try:
                        rss_total += c.memory_info().rss
                    except Exception:                # noqa: BLE001
                        pass
            except Exception:                        # noqa: BLE001
                pass
        free = psutil.virtual_memory().available
    except ImportError:
        pass                                         # psutil absent: sizes 0
    # warm_count is EVERY warm-origin process (parked + serving): counting
    # parked alone structurally hid each serving process and reported half
    # the real memory in process-cache-2's two-stub probe — understating the
    # ceiling in the reassuring direction, on the number the user asked for
    _journal("pool", warm_count=len(entries), parked=len(parked),
             serving=len(serving), eligible=elig_total,
             rss_total_mb=round(rss_total / 1048576, 1),
             free_ram_mb=round(free / 1048576, 1), evictions_total=0)


def keeper_pass_now() -> None:
    """Synchronous single pass — the test seam process-cache-2 asked for:
    deterministic reconcile without waiting on the keeper's own clock."""
    _keeper_pass()


def _keeper() -> None:
    last_snap = 0.0
    while True:
        _poke.wait(WARM_POLL)
        _poke.clear()
        try:
            _keeper_pass()
        except Exception as e:                      # noqa: BLE001
            print(f"[orgtree] warmpool keeper pass failed: "
                  f"{type(e).__name__}: {e}")
        if time.time() - last_snap >= POOL_SNAP_EVERY:
            last_snap = time.time()
            try:
                _pool_snapshot()
            except Exception:                       # noqa: BLE001
                pass


def start_warm_pool() -> None:
    """Boot engine, same singleton shape as the other start_* loops. The
    first keeper pass IS the boot pre-warm: every live eligible agent gets a
    parked process before any turn arrives (user ruling: all of them, no
    subset, no cap — if the memory does not fit, that is measured by the
    pool snapshots and ruled on by a human, not quietly capped here)."""
    global _started
    if _started:
        return
    _started = True
    store.save_hooks.append(lambda _slug: _poke.set())
    # THE FIRST PASS RUNS SYNCHRONOUSLY, before this returns and therefore
    # before any driver (auto-resume, reconcile's re-drives, the first API
    # request) can start a turn. The user's ruling is "start all active
    # agents' processes immediately on orgtree launch, BEFORE a turn begins"
    # — an async first pass made the feature absent at exactly the moment it
    # was specified to be present, on every one of ~10 restarts a day
    # (process-cache-2's finding, coordinator-ruled).
    try:
        _keeper_pass()
    except Exception as e:                          # noqa: BLE001
        print(f"[orgtree] warmpool boot pass failed: {type(e).__name__}: {e}")
    threading.Thread(target=_keeper, daemon=True, name="warmpool").start()
