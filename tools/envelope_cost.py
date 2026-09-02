"""Measure what the per-turn dynamic envelope actually costs, from real
transcripts. The evidence behind D-223.

    python tools/envelope_cost.py                # inventory + churn
    python tools/envelope_cost.py --days 3       # only recent transcripts
    python tools/envelope_cost.py --simulate     # replay through D-223's rules
    python tools/envelope_cost.py --sweep        # threshold sensitivity

WHY THIS IS A TOOL AND NOT A ONE-OFF
------------------------------------
D-223 rests on numbers — "the chart is 51% of ORG STATE", "only 15.8% of it
changes per turn", "60k tokens is the knee of the threshold curve". A ruling
whose evidence cannot be re-derived is a ruling nobody can challenge or
refresh, and these particular numbers WILL drift: they are properties of how
this org happens to be shaped today. Re-run this before trusting them, and
before tuning any threshold in `orgtree/envelope.py`.

⚠ CHARACTERS ARE EXACT. TOKENS ARE NOT.
No tokenizer ships with this repo and none is reachable offline, so this tool
reports characters and leaves tokens alone. Two attempts to calibrate
chars→tokens against the provider's own accounting both produced impossible
ratios, and both failures are worth knowing about because they look plausible:

  * warm-resume `cache_creation_input_tokens` → 0.63 chars/token. Cache writes
    are block-quantised; the field covers a whole re-written block, not the
    span you appended.
  * total-prompt growth minus the previous reply's `output_tokens` →
    2.10 chars/token. The harness injects system-reminders that never appear
    in the transcript, so the growth is real but the characters are not all
    visible here.

If you need tokens, multiply by a stated ratio and SAY it is an estimate. The
percentages below are ratios and are tokenizer-independent, which is why the
ruling leans on those.

READ-ONLY. Touches nothing but its own stdout.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict

PROJECTS = os.path.expanduser("~/.claude/projects")

#: Envelope sections, by their opening and closing markers. These are the
#: literals in `supervisor.ORG_STATE_OPEN`/`turnusage.OPEN`/`_mail_block`; if
#: one of those is renamed this tool goes quiet rather than wrong, so the
#: section counts dropping to zero is the signal to come and fix it here.
MARKERS = (
    ("ORG_STATE", "[ORG STATE", "[END ORG STATE]"),
    ("PROVIDER_USAGE", "[PROVIDER USAGE", "[END PROVIDER USAGE]"),
    ("NOTICES", "[ORG NOTICES", "[END NOTICES]"),
    ("MAIL", "[MAIL —", "[END MAIL]"),
)

#: The fields that move every turn without telling an agent anything: ISO
#: stamps, relative countdowns, observation ages. Masking these is what
#: separates "these bytes differ" from "this block means something new".
VOLATILE = (
    (re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?"), "<TS>"),
    (re.compile(r"\((?:[+-]\d+[dhm](?:\d+[hm])?|<1m)\)"), "<CD>"),
    (re.compile(r"\((?:\d+s|\?|live),?(?:fresh|stale)?\)"), "<AGE>"),
)

# D-223's live thresholds, mirrored for the simulator. Kept as arguments
# rather than imported so this tool can answer "what WOULD 100k have saved".
MAX_TURNS, MAX_AGE_S, MAX_TOKENS = 10, 900.0, 60_000


def mask(text: str) -> str:
    for rx, sub in VOLATILE:
        text = rx.sub(sub, text)
    return text


def text_of(message: object) -> str:
    if isinstance(message, dict):
        message = message.get("content")
    if isinstance(message, str):
        return message
    out: list[str] = []
    if isinstance(message, list):
        for block in message:
            if isinstance(block, str):
                out.append(block)
            elif isinstance(block, dict):
                kind = block.get("type")
                if kind == "text":
                    out.append(str(block.get("text") or ""))
                elif kind == "tool_result":
                    body = block.get("content")
                    out.append(body if isinstance(body, str)
                               else json.dumps(body))
                elif kind == "image":
                    out.append("<IMAGE>")
    return "\n".join(out)


def sections(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for name, opener, closer in MARKERS:
        i = text.find(opener)
        if i < 0:
            continue
        j = text.find(closer, i)
        if j >= 0:
            found[name] = text[i:j + len(closer)]
    return found


def transcripts(days: float | None, prefix: str) -> list[str]:
    cutoff = 0.0 if days is None else time.time() - days * 86400
    out: list[str] = []
    if not os.path.isdir(PROJECTS):
        return out
    for entry in os.listdir(PROJECTS):
        if prefix and prefix not in entry:
            continue
        folder = os.path.join(PROJECTS, entry)
        if not os.path.isdir(folder):
            continue
        for name in os.listdir(folder):
            if not name.endswith(".jsonl"):
                continue
            path = os.path.join(folder, name)
            try:
                if os.path.getmtime(path) >= cutoff:
                    out.append(path)
            except OSError:
                pass
    return out


def envelopes(paths: list[str]):
    """Yield (path, occupancy, timestamp, {section: body}) per enveloped turn.

    `occupancy` is the prompt size the provider reported for the most recent
    request before this turn — the simulator's stand-in for the node's own
    occupancy, which is not recorded in the transcript.
    """
    for path in paths:
        occ = 0
        try:
            handle = open(path, encoding="utf-8")
        except OSError:
            continue
        with handle:
            for line in handle:
                try:
                    rec = json.loads(line)
                except Exception:                            # noqa: BLE001
                    continue
                if rec.get("type") == "assistant":
                    usage = (rec.get("message") or {}).get("usage") or {}
                    try:
                        occ = (int(usage.get("input_tokens") or 0)
                               + int(usage.get("cache_read_input_tokens") or 0)
                               + int(usage.get(
                                   "cache_creation_input_tokens") or 0))
                    except (TypeError, ValueError):
                        pass
                    continue
                if rec.get("type") != "user":
                    continue
                found = sections(text_of(rec.get("message")))
                if found:
                    yield path, occ, str(rec.get("timestamp") or ""), found


def epoch(stamp: str) -> float:
    try:
        import datetime as dt
        return dt.datetime.fromisoformat(
            stamp.replace("Z", "+00:00")).timestamp()
    except Exception:                                        # noqa: BLE001
        return 0.0


def inventory(paths: list[str]) -> None:
    chars: dict[str, int] = defaultdict(int)
    count: dict[str, int] = defaultdict(int)
    turns = 0
    sessions: set[str] = set()
    parts: Counter[str] = Counter()
    org_state_renders = 0

    for path, _occ, _at, found in envelopes(paths):
        turns += 1
        sessions.add(path)
        for name, body in found.items():
            chars[name] += len(body)
            count[name] += 1
        block = found.get("ORG_STATE")
        if block:
            org_state_renders += 1
            for raw in block.splitlines():
                parts[classify(raw.rstrip())] += len(raw) + 1

    print("=" * 72)
    print(f"ENVELOPE INVENTORY — {turns:,} enveloped turns, "
          f"{len(sessions)} transcripts")
    print("=" * 72)
    print(f"{'section':<17}{'turns':>8}{'freq':>7}{'chars/turn':>12}"
          f"{'total chars':>14}")
    for name, _o, _c in MARKERS:
        if not count[name]:
            continue
        print(f"{name:<17}{count[name]:>8,}"
              f"{100 * count[name] / max(1, turns):>6.0f}%"
              f"{chars[name] / count[name]:>12,.0f}{chars[name]:>14,}")
    total = sum(chars.values())
    print("-" * 72)
    print(f"{'ALL SECTIONS':<17}{'':>8}{'':>7}"
          f"{total / max(1, turns):>12,.0f}{total:>14,}")

    if org_state_renders:
        print(f"\nORG STATE composition, chars/turn over "
              f"{org_state_renders:,} renderings:")
        whole = sum(parts.values()) or 1
        for key in sorted(parts):
            print(f"   {key:<24}{parts[key] / org_state_renders:>8.0f}"
                  f"{100 * parts[key] / whole:>7.1f}%")


def classify(line: str) -> str:
    if line.startswith("[ORG STATE"):
        return "1 header"
    if line.startswith("Your reports"):
        return "2 roster"
    if line.startswith(("The full organization", "Your full sub")):
        return "3 chart title"
    if line.startswith(("- ", "  ", "+ ", "(")):
        return "4 chart + archived"
    if line.startswith("Credits:"):
        return "5 credits"
    if line.startswith("[END"):
        return "9 close"
    if line.startswith("⚠"):
        return "7 open ask"
    if line.startswith("Note"):
        return "6 guidance"
    return "8 other"


def churn(paths: list[str]) -> None:
    """How much of each block is genuinely different from the turn before."""
    stats: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    previous: dict[tuple[str, str], str] = {}
    for path, _occ, _at, found in envelopes(paths):
        for name in ("ORG_STATE", "PROVIDER_USAGE"):
            body = found.get(name)
            if body is None:
                continue
            old = previous.get((path, name))
            previous[(path, name)] = body
            if old is None:
                continue
            row = stats[name]
            row[0] += 1
            row[1] += len(body)
            for masked, index in ((False, 2), (True, 3)):
                a = [mask(x) if masked else x for x in old.splitlines()]
                b = [mask(x) if masked else x for x in body.splitlines()]
                fresh = body.splitlines()
                diff = difflib.SequenceMatcher(None, a, b, autojunk=False)
                for op, _i1, _i2, j1, j2 in diff.get_opcodes():
                    if op == "equal":
                        continue
                    for k in range(j1, min(j2, len(fresh))):
                        row[index] += len(fresh[k]) + 1

    print("\n" + "=" * 72)
    print("TURN-OVER-TURN CHURN — what a delta would actually have to re-send")
    print("=" * 72)
    print(f"{'block':<17}{'pairs':>8}{'byte-diff':>12}{'semantic-diff':>16}")
    for name in ("ORG_STATE", "PROVIDER_USAGE"):
        pairs, total, raw, masked_diff = stats[name]
        if not pairs:
            continue
        print(f"{name:<17}{pairs:>8,}{100 * raw / max(1, total):>11.1f}%"
              f"{100 * masked_diff / max(1, total):>15.1f}%")
    print("\nsemantic-diff masks timestamps, countdowns and observation ages.")
    print("The gap between the two columns is the whole argument for D-223:")
    print("the bytes move every turn, the meaning does not.")


def simulate(paths: list[str], max_turns: int, max_age: float,
             max_tokens: int, quiet: bool = False) -> tuple[int, int]:
    """Replay real blocks through D-223's rules. Returns (old, new) chars."""
    old_total = new_total = 0
    reasons: Counter[str] = Counter()
    state: dict[tuple[str, str], dict[str, float]] = {}
    seqs: dict[tuple[str, str], int] = defaultdict(int)

    for path, occ, at, found in envelopes(paths):
        now = epoch(at)
        for name in ("ORG_STATE", "PROVIDER_USAGE"):
            body = found.get(name)
            if body is None:
                continue
            old_total += len(body)
            key = (path, name)
            if name == "ORG_STATE":
                chart = [ln for ln in body.splitlines()
                         if ln.startswith(("- ", "  ", "+ ", "("))
                         or ln.startswith(("The full organization",
                                           "Your full sub"))]
                digest = hashlib.sha1(
                    "\n".join(chart).encode("utf-8", "replace")).hexdigest()
                suppressible = sum(len(x) + 1 for x in chart)
            else:
                digest = hashlib.sha1(
                    mask(body).encode("utf-8", "replace")).hexdigest()
                suppressible = len(body)
            prior = state.get(key)
            why = None
            if prior is None:
                why = "first"
            elif prior["dig"] != digest:                     # type: ignore[index]
                why = "changed"
            elif occ and prior["occ"] and occ < prior["occ"]:
                why = "context-shrank"
            elif occ and prior["occ"] and occ - prior["occ"] >= max_tokens:
                why = "token-threshold"
            elif prior["turns"] + 1 > max_turns:
                why = "turn-threshold"
            elif now and prior["at"] and now - prior["at"] >= max_age:
                why = "age-threshold"
            if why:
                reasons[f"{name}:{why}"] += 1
                seqs[key] += 1
                state[key] = {"dig": digest, "at": now,     # type: ignore[dict-item]
                              "occ": occ, "turns": 0}
                new_total += len(body)
            else:
                state[key]["turns"] += 1
                # the pointer that replaces the span, measured not guessed
                pointer = (90 if name == "ORG_STATE" else 150)
                new_total += len(body) - suppressible + pointer

    if not quiet:
        print("\n" + "=" * 72)
        print(f"D-223 SIMULATION — {max_turns} turns / {max_age:g}s / "
              f"{max_tokens:,} tokens")
        print("=" * 72)
        saved = old_total - new_total
        print(f"  {old_total:,} chars -> {new_total:,} chars "
              f"(saved {saved:,} = {100 * saved / max(1, old_total):.1f}%)")
        print("\n  full-refresh reasons:")
        for key, value in reasons.most_common():
            print(f"     {key:<32}{value:>7,}")
    return old_total, new_total


def sweep(paths: list[str]) -> None:
    print("\n" + "=" * 72)
    print("THRESHOLD SENSITIVITY — where the saving stops improving")
    print("=" * 72)
    print(f"{'tokens':>10}{'turns':>8}{'saved':>12}{'saved %':>10}")
    for tokens in (25_000, 60_000, 100_000, 250_000):
        for turn_cap in (10, 25):
            old, new = simulate(paths, turn_cap, MAX_AGE_S, tokens, quiet=True)
            print(f"{tokens:>10,}{turn_cap:>8}{old - new:>12,}"
                  f"{100 * (old - new) / max(1, old):>9.1f}%")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__ and __doc__.split("\n")[0])
    ap.add_argument("--days", type=float, default=None,
                    help="only transcripts modified in the last N days")
    ap.add_argument("--prefix", default="orgtree-scratch-orgtree",
                    help="project-directory substring to scan")
    ap.add_argument("--simulate", action="store_true")
    ap.add_argument("--sweep", action="store_true")
    args = ap.parse_args()

    paths = transcripts(args.days, args.prefix)
    if not paths:
        print(f"no transcripts under {PROJECTS} matching {args.prefix!r}")
        return 1
    inventory(paths)
    churn(paths)
    if args.simulate:
        simulate(paths, MAX_TURNS, MAX_AGE_S, MAX_TOKENS)
    if args.sweep:
        sweep(paths)
    return 0


if __name__ == "__main__":
    sys.exit(main())
