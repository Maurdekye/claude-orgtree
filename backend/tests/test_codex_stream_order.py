"""THE ORDERING INVARIANT, on the codex lane.

    python backend/tests/test_codex_stream_order.py   (no pytest; plain asserts)

INVARIANT: no assistant output for a turn may become VISIBLE before that
turn's user message is DURABLE in the transcript.

Claude gets this from its provider — the CLI owns the transcript and writes
the user record into it before it emits anything of its own. A codex thread
has no CLI-owned transcript; orgtree journals it, so on this lane the
invariant is orgtree's to keep.

It was not kept, and the desk showed exactly what that costs. `_codex_leg`
opened its journal on the RETURN of `turn.start()`, but `AppServerClient._pump`
dispatches notifications on the READER thread while the turn thread is still
inside `request()`'s 20 ms poll loop — so `item/started`,
`item/agentMessage/delta` and `item/completed` were all observed before that
return, on every measured run, fresh threads and resumed alike. Durable
records buffered in memory meanwhile, so for that window the transcript held
no user row for the turn. The desk draws the durable block first, the live
tail under it and the user's own undelivered message at the very BOTTOM
(`pending_mail`) — so the agent's answer rendered above the question, while
the question still read "delivering…".

What this suite asserts is the invariant itself, at the only boundary that
matters: `supervisor.stream` is the websocket the desk sees, so every payload
crossing it is checked against the journal ON DISK at that instant. Ordering
that merely happens to hold cannot pass — the fixture makes the race certain.

Anti-vacuity is threefold, because an ordering test that watches nothing is
the easiest test in the world to pass by accident:
  §1 asserts the early_stream fixture ACTUALLY raced (the whole turn reaches
     the wire before `turn/start` is answered — proven by the reply order the
     scenario is built on, and by the visible payloads arriving at all)
  §5 feeds the checker a synthetic pre-commit emission and requires it to
     REPORT it — an instrument that cannot fail is not evidence
  every section asserts a non-zero count of assistant-visible payloads, so a
  fixture that quietly emits nothing fails instead of passing by silence
"""

import glob
import json
import os
import sys
import tempfile
import traceback

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

DATA = tempfile.mkdtemp(prefix="orgtree-codexorder-")
os.environ["ORGTREE_DATA"] = DATA
os.environ["ORGTREE_WARM"] = "0"       # cold spawns only: one process per turn
# a PORT NOBODY SERVES — the codex leg's tool dispatcher POSTs /api/agent, and
# left unset it would default to 7360 and reach the operator's LIVE backend
os.environ["ORGTREE_PORT"] = "9"
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

FAKECODEX = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "fakecodex.py")
CODEX_HOME = tempfile.mkdtemp(prefix="codexorder-home-")
os.environ["ORGTREE_CODEX"] = FAKECODEX
os.environ["CODEX_HOME"] = CODEX_HOME
with open(os.path.join(CODEX_HOME, "auth.json"), "w", encoding="utf-8") as _f:
    _f.write('{"tokens": {}}')

from orgtree import providers, store, supervisor                   # noqa: E402
from orgtree.ledger import USER                                    # noqa: E402

providers._status_cache = None
supervisor.CODEX_STEER_POLL = 0.2

PASS = 0
FAIL: list[tuple[str, str]] = []

#: assistant output as the DESK sees it — a live row (text/tool/thought) or a
#: token delta. `journal` is a refetch nudge and `mcp_tool_count` is chrome;
#: neither is the agent speaking, so neither is gated.
VISIBLE_KINDS = {"delta", "text", "tool", "thought"}


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


def eq(got, want, what):
    if got != want:
        raise AssertionError(f"{what}: got {got!r}, wanted {want!r}")


# ── the instrument ──────────────────────────────────────────────────────────
def journal_records(slug: str) -> list[dict]:
    """Every record orgtree has journaled for this org, in file order."""
    out: list[dict] = []
    pat = os.path.join(supervisor.journal_store(), "projects", slug, "*.jsonl")
    for path in sorted(glob.glob(pat)):
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(rec, dict):
                        out.append(rec)
        except OSError:
            pass
    return out


def is_user_prompt(rec: dict) -> bool:
    """A TURN's user row — not a tool_result, which is also `type: user` but
    carries a content LIST rather than the prompt string."""
    msg = rec.get("message")
    return (rec.get("type") == "user" and isinstance(msg, dict)
            and isinstance(msg.get("content"), str))


def user_prompts(slug: str) -> int:
    return sum(1 for r in journal_records(slug) if is_user_prompt(r))


#: (kind, user prompt rows durable AT THE INSTANT this payload was emitted)
SEEN: list[tuple[str, int]] = []
WATCH: dict[str, str] = {"slug": ""}


def recording_stream(slug: str, nid: str, payload: dict) -> None:
    kind = str(payload.get("kind") or "")
    if WATCH["slug"] and slug == WATCH["slug"]:
        SEEN.append((kind, user_prompts(slug)))


supervisor.stream = recording_stream


def visible(seen: list[tuple[str, int]] | None = None) -> list[tuple[str, int]]:
    return [(k, n) for k, n in (SEEN if seen is None else seen)
            if k in VISIBLE_KINDS]


def violations(seen: list[tuple[str, int]] | None = None,
               need: int = 1) -> list[tuple[str, int]]:
    """Assistant-visible payloads that reached the desk while fewer than
    `need` user prompt rows were durable — i.e. the answer outran its own
    question."""
    return [(k, n) for k, n in visible(seen) if n < need]


# ── fixtures ────────────────────────────────────────────────────────────────
def mkorg(label: str) -> tuple[str, str]:
    org = store.create_org(f"zz codexorder {label}")
    r = org.hire(USER, None, "sol", 0, "cx", add_dirs=[],
                 tools={"bash": True, "web": False, "edit": True,
                        "subagents": False, "mcp": []},
                 org_visibility="team", charter="a codex ordering test agent")
    nid = r["node"]
    store.save_org(org)
    return org.d["slug"], nid


def run_turn(slug: str, nid: str, text: str):
    st = supervisor.state(slug, nid)
    with supervisor._state_lock:
        st["busy"] = True
    return supervisor._run_one_turn(slug, nid, {"text": text, "view": text})


def scenario(name: str, thread: str) -> None:
    """Pick the fixture's behaviour AND give this section its own thread id.

    The thread id matters more than it looks. `transcript_path` globs
    `projects/*/<session>.jsonl` — across ALL projects, because a session id
    is unique in production. fakecodex's default is a constant, so two test
    orgs in one run both record `fake-thread-0001` and the glob hands the
    second org the FIRST one's journal. That is a fixture collision, not a
    product fact, and it would otherwise show up here as a fake gap."""
    os.environ["FAKECODEX_SCENARIO"] = name
    os.environ["FAKECODEX_THREAD_ID"] = thread


def main() -> int:
    print("§1 stream-before-commit: the whole turn races the turn/start reply")
    scenario("early_stream", "fake-thread-order-early")
    slug, nid = mkorg("early")
    SEEN.clear()
    WATCH["slug"] = slug
    run_turn(slug, nid, "does the answer wait for the question?")
    early = list(SEEN)

    check("the fixture actually produced assistant-visible output "
          "(anti-vacuity: an empty stream must not pass)",
          lambda: (_ for _ in ()).throw(AssertionError(
              f"no visible payloads at all; got kinds "
              f"{sorted({k for k, _ in early})}"))
          if not visible(early) else None)
    check("every visible kind the leg can emit was exercised "
          "(delta, durable text row, thought row)",
          lambda: eq(sorted({k for k, _ in visible(early)}),
                     ["delta", "text", "thought"], "visible kinds"))
    check("NO assistant output reached the desk before the user's row was "
          "durable",
          lambda: eq(violations(early), [], "pre-commit emissions"))

    print("§2 the journal itself is ordered: question above answer")
    recs = journal_records(slug)
    check("the turn's user row is the FIRST record in the journal",
          lambda: (eq(is_user_prompt(recs[0]), True,
                      "first journal record is the user prompt")
                   if recs else (_ for _ in ()).throw(
                       AssertionError("the journal is empty"))))
    check("exactly one user prompt row for one turn (no duplicate commit)",
          lambda: eq(user_prompts(slug), 1, "user prompt rows"))
    # the RAW record carries the whole provider prompt — turn envelope first,
    # the person's words last. That is the record's job; the human projection
    # lives in the prompt-view sidecar and is what read_chat renders (§5). So
    # the assertion is that the user's words are IN the committed row and are
    # the last thing in it, not that the row is only those words.
    check("the committed user row ends with the words that were sent",
          lambda: eq(str(recs[0]["message"]["content"]).endswith(
              "does the answer wait for the question?"), True,
              "the prompt's tail is the user's message"))
    check("no assistant record precedes the user's row in the journal",
          lambda: eq([r.get("type") for r in recs].index("assistant") > 0,
                     True, "first assistant record is after the user's"))

    print("§3 commit-before-stream: the ordinary path keeps the same order")
    scenario("tool", "fake-thread-order-normal")
    slug2, nid2 = mkorg("normal")
    SEEN.clear()
    WATCH["slug"] = slug2
    run_turn(slug2, nid2, "ordinary turn")
    normal = list(SEEN)
    check("the ordinary scenario also produced visible output",
          lambda: (_ for _ in ()).throw(AssertionError("no visible payloads"))
          if not visible(normal) else None)
    check("…and none of it preceded the user's durable row",
          lambda: eq(violations(normal), [], "pre-commit emissions"))
    check("a tool row is gated too, not just prose",
          lambda: eq(any(k == "tool" for k, _ in visible(normal)), True,
                     "a tool row was emitted"))

    print("§4 rapid consecutive turns: each answer waits for ITS OWN question")
    scenario("early_stream", "fake-thread-order-rapid")
    slug3, nid3 = mkorg("rapid")
    WATCH["slug"] = slug3
    SEEN.clear()
    run_turn(slug3, nid3, "first message")
    first = list(SEEN)
    SEEN.clear()
    run_turn(slug3, nid3, "second message")
    second = list(SEEN)
    check("turn 1: no output before one user row was durable",
          lambda: eq(violations(first, need=1), [], "turn 1 pre-commit"))
    check("turn 2 (a RESUMED thread): no output before the SECOND user row "
          "was durable — the resume path is where the id is known earliest "
          "and was still committed latest",
          lambda: eq(violations(second, need=2), [], "turn 2 pre-commit"))
    check("two turns leave exactly two user rows — no gap, no duplicate",
          lambda: eq(user_prompts(slug3), 2, "user prompt rows"))
    check("both turns produced visible output",
          lambda: eq(bool(visible(first)) and bool(visible(second)), True,
                     "visible output on both turns"))

    print("§5 the transcript the desk actually renders")
    payload = supervisor.read_chat(store.load_org(slug3), nid3)
    roles = [m["role"] for m in payload["messages"]]
    texts = [m.get("text") or "" for m in payload["messages"]]
    check("the rendered transcript opens with the user, not the agent",
          lambda: eq(roles[0] if roles else None, "user", "first rendered row"))
    check("both user messages render, in the order they were sent",
          lambda: eq([t for t, r in zip(texts, roles) if r == "user"],
                     ["first message", "second message"], "user rows"))
    check("the agent's answer renders exactly once per turn — no double "
          "rendering between the live tail and the transcript, and no gap",
          lambda: eq(sum(1 for t in texts if "answering before the reply" in t)
                     + sum(1 for r in payload["live"]
                           if "answering before the reply"
                           in str(r.get("text") or "")),
                     2, "copies of the answer across two turns"))
    check("every assistant row is preceded by a user row — the rendered "
          "transcript never opens a turn with the answer",
          lambda: eq([i for i, r in enumerate(roles)
                      if r == "assistant"
                      and "user" not in roles[:i]], [],
                     "assistant rows with no user row above them"))

    print("§6 duplicated / replayed events render once")
    scenario("replay", "fake-thread-order-replay")
    slug4, nid4 = mkorg("replay")
    SEEN.clear()
    WATCH["slug"] = slug4
    run_turn(slug4, nid4, "say it once")
    replayed = list(SEEN)
    pay4 = supervisor.read_chat(store.load_org(slug4), nid4)
    j4 = journal_records(slug4)
    check("a replayed agentMessage completion is journaled ONCE",
          lambda: eq(sum(1 for r in j4
                         for b in ((r.get("message") or {}).get("content") or [])
                         if isinstance(b, dict)
                         and b.get("text") == "said exactly once"),
                     1, "durable copies of the replayed message"))
    check("a replayed reasoning completion is journaled ONCE",
          lambda: eq(sum(1 for r in j4
                         for b in ((r.get("message") or {}).get("content") or [])
                         if isinstance(b, dict)
                         and b.get("type") == "thinking"),
                     1, "durable thinking rows"))
    check("…and it reaches the desk once too — one live row, not two",
          lambda: eq(sum(1 for k, _ in visible(replayed) if k == "text"),
                     1, "live text rows emitted"))
    check("the replayed turn still keeps the ordering invariant",
          lambda: eq(violations(replayed), [], "pre-commit emissions"))
    check("the answer renders exactly once in the desk payload",
          lambda: eq(sum(1 for m in pay4["messages"]
                         if "said exactly once" in str(m.get("text") or ""))
                     + sum(1 for r in pay4["live"]
                           if "said exactly once" in str(r.get("text") or "")),
                     1, "rendered copies of the answer"))

    print("§7 anti-vacuity: the instrument can SEE a violation")
    check("a synthetic pre-commit emission is reported, so a clean run means "
          "clean and not blind",
          lambda: eq(violations([("delta", 0), ("text", 1)], need=1),
                     [("delta", 0)], "detected violations"))

    print()
    for label, tb in FAIL:
        print(f"--- FAILED: {label}\n{tb}")
    print(f"{PASS} checks passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
