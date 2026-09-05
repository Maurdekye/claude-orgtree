"""Sidecar provenance (v2): the projection states the ORIGIN of its own bytes.

    python backend/tests/test_prompt_view_spans.py   (no pytest; asserts)

WHY THIS EXISTS. The read-time historical conversion of chat timestamps was
withdrawn because a stored row could not say where its own characters came
from: a no-mail turn's AUTHORED view can be byte-for-byte identical to a
generated mail envelope at offset 0, so every scanner that tried to tell them
apart by shape would eventually rewrite words a person or an agent wrote.
The conclusion was that a safe conversion needs INDEPENDENT PERSISTED
LINKAGE, not a better matcher.

This suite pins that linkage. `_human_view_spans` reports the span it just
generated, at composition, from the durable mail rows it rendered; the row
carries it; `_prompt_view_spans` validates it on the way back out.

WARNING - THE CENTRAL TEST IS `test_the_counterexample_pair`. It builds two
rows whose `visible` strings BEGIN WITH THE SAME CHARACTERS - one because an
envelope was generated into it, one because a human authored exactly that
text - and requires the two to be told apart. Any implementation that
inspects the text instead of the record fails it in one direction or the
other, which is precisely what makes it a check rather than a decoration.

Hermetic: a throwaway ORGTREE_DATA established BEFORE `store` is imported
(binding it at import time to the live root is how seven orgs once leaked
into the operator's data), no network, no CLI.
"""

import hashlib
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

DATA = tempfile.mkdtemp(prefix="orgtree-pvspans-")
os.environ["ORGTREE_DATA"] = DATA
os.environ["ORGTREE_PORT"] = "9"
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

from orgtree import store, supervisor          # noqa: E402

# the storage guard, asserted rather than assumed (team rule): establish that
# this process CANNOT resolve to the operator's live root before writing.
LIVE = os.path.join(os.path.expanduser("~"), "orgtree")
assert os.path.realpath(store.DATA_ROOT) == os.path.realpath(DATA), \
    f"DATA_ROOT bound to {store.DATA_ROOT}, not the throwaway {DATA}"
assert os.path.realpath(store.DATA_ROOT) != os.path.realpath(LIVE), \
    "refusing to run: this process resolves to the LIVE data root"

FAILED: list[str] = []


def check(ok: object, label: str) -> None:
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        FAILED.append(label)


def mail(mid: str, body: str) -> dict[str, object]:
    return {"id": mid, "from": "coordinator", "kind": "request", "body": body,
            "at": "2026-09-05T06:33:31.362Z", "relationship": "your superior"}


def envelope_of(entries: list[dict[str, object]]) -> str:
    return supervisor._mail_block(entries, "mine", "agent", inline=True,
                                  human=True)[0]


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- composition
def test_composition_reports_the_span_it_generated() -> None:
    print("composition")
    entries = [mail("aaaaaaaaaaaa", "first"), mail("bbbbbbbbbbbb", "second")]
    view, spans = supervisor._human_view_spans(
        entries, "what the agent was already going to say", "mine", "agent",
        inline=True)
    check(len(spans) == 1, f"one generated span: {spans}")
    s = spans[0]
    check(s["start"] == 0, f"the envelope starts at 0: {s['start']}")
    check(view[s["start"]:s["end"]] == envelope_of(entries),
          "the span's characters ARE the envelope the formatter produced")
    check(s["mail_ids"] == ["aaaaaaaaaaaa", "bbbbbbbbbbbb"],
          f"both durable mail ids are recorded, in order: {s['mail_ids']}")
    check(view[s["end"]:] == "\n\nwhat the agent was already going to say",
          "everything after the span is the authored remainder, untouched")
    # POSITIVE CONTROL for the span itself: a DIFFERENT mail set must move the
    # boundary. A span that is the same number whatever the input is not a
    # measurement.
    _, spans2 = supervisor._human_view_spans(
        [mail("cccccccccccc", "a very much longer body " * 8)], "tail",
        "mine", "agent", inline=True)
    check(spans2[0]["end"] != s["end"],
          f"CONTROL: a different envelope yields a different end "
          f"({spans2[0]['end']} vs {s['end']}) - the offset is measured, "
          f"not constant")


def test_no_mail_is_a_positive_empty_not_unknown() -> None:
    print("no-mail composition")
    view, spans = supervisor._human_view_spans(
        [], "just the agent's own words", "mine", "agent", inline=True)
    check(view == "just the agent's own words", "the view is the base view")
    check(spans == [], f"spans is [] - a POSITIVE 'nothing generated': {spans}")
    check(spans is not None, "and it is emphatically not None")


# ---------------------------------------------------------------- the row
def test_row_v2_carries_spans_and_binds_them() -> None:
    print("row")
    supervisor._record_prompt_view("mine", "sess-v2", "RAW", "VISIBLE",
                                   at="2026-09-05T06:00:00Z", spans=[])
    supervisor._record_prompt_view("mine", "sess-v1", "RAW", "VISIBLE",
                                   at="2026-09-05T06:00:00Z")
    rows = supervisor._load_prompt_views("mine", "sess-v2")
    row = next(iter(rows.values()))[0]
    check(row["v"] == 2, f"a row written WITH spans is v2: {row.get('v')}")
    check("vsha256" in row, "it binds its spans to the visible it measured")
    old = next(iter(supervisor._load_prompt_views(
        "mine", "sess-v1").values()))[0]
    check(old["v"] == 1 and "spans" not in old,
          f"a row written WITHOUT spans stays v1 and says nothing: {old}")
    check(supervisor._prompt_view_spans(old) is None,
          "and reads back as UNKNOWN, never as 'no envelope'")


# ------------------------------------------------------- THE COUNTEREXAMPLE
def test_the_counterexample_pair() -> None:
    """The case that withdrew the historical converter, now decided."""
    print("the counterexample pair (authored text identical to an envelope)")
    entries = [mail("dddddddddddd", "please look at the deployed build")]
    env = envelope_of(entries)

    generated_view, spans = supervisor._human_view_spans(
        entries, "", "mine", "agent", inline=True)
    # the AUTHORED row: a person (or an agent) wrote, verbatim, the very same
    # characters - quoting an envelope back is an ordinary thing to do here
    authored_view = env
    check(authored_view == generated_view,
          "the two views are byte-for-byte identical (no scanner can split "
          "them)")

    gen_row = {"v": 2, "visible": generated_view, "spans": spans,
               "vsha256": sha(generated_view)}
    auth_row = {"v": 2, "visible": authored_view, "spans": [],
                "vsha256": sha(authored_view)}

    got_gen = supervisor._prompt_view_spans(gen_row)
    got_auth = supervisor._prompt_view_spans(auth_row)
    check(got_gen is not None and len(got_gen) == 1
          and got_gen[0]["start"] == 0 and got_gen[0]["end"] == len(env),
          f"the GENERATED row reports its envelope span: {got_gen}")
    check(got_gen is not None and got_gen[0]["mail_ids"] == ["dddddddddddd"],
          "and links it to the durable mail row it was rendered from")
    check(got_auth == [],
          f"the AUTHORED row reports NO generated bytes: {got_auth}")
    check(got_gen != got_auth,
          "SAME BYTES, OPPOSITE ANSWERS - decided by the record, not the text")


# ---------------------------------------------------------------- validation
def test_a_claim_that_no_longer_binds_is_refused() -> None:
    print("validation")
    entries = [mail("eeeeeeeeeeee", "body")]
    view, spans = supervisor._human_view_spans(entries, "tail", "mine",
                                               "agent", inline=True)
    good = {"v": 2, "visible": view, "spans": spans, "vsha256": sha(view)}
    check(supervisor._prompt_view_spans(good) is not None,
          "CONTROL: the intact row validates (so the refusals below mean "
          "something)")

    moved = dict(good, visible="x" + view)
    check(supervisor._prompt_view_spans(moved) is None,
          "a view edited after measurement: refused (stale offsets)")
    check(supervisor._prompt_view_spans(dict(good, vsha256="deadbeef")) is None,
          "a hash that does not match: refused")
    end = spans[0]["end"]
    for label, bad in (
        ("past the end of the string",
         [{"kind": "mail_envelope", "start": 0, "end": len(view) + 1}]),
        ("inverted", [{"kind": "mail_envelope", "start": end, "end": 0}]),
        ("negative", [{"kind": "mail_envelope", "start": -1, "end": end}]),
        ("overlapping",
         [{"kind": "mail_envelope", "start": 0, "end": end},
          {"kind": "mail_envelope", "start": end - 1, "end": end}]),
        ("not a number",
         [{"kind": "mail_envelope", "start": "0", "end": end}]),
        ("a bool posing as an offset",
         [{"kind": "mail_envelope", "start": False, "end": end}]),
        ("not a dict", ["nonsense"]),
    ):
        row = dict(good, spans=bad)
        check(supervisor._prompt_view_spans(row) is None, f"{label}: refused")
    check(supervisor._prompt_view_spans({"v": 1, "visible": view}) is None,
          "a v1 row: UNKNOWN (this is the historical corpus, left alone)")
    check(supervisor._prompt_view_spans({}) is None, "an empty row: unknown")


for fn in (test_composition_reports_the_span_it_generated,
           test_no_mail_is_a_positive_empty_not_unknown,
           test_row_v2_carries_spans_and_binds_them,
           test_the_counterexample_pair,
           test_a_claim_that_no_longer_binds_is_refused):
    fn()

print()
if FAILED:
    print(f"FAIL - {len(FAILED)} check(s):")
    for f in FAILED:
        print("  -", f)
    raise SystemExit(1)
print("PASS - sidecar provenance states origin, and refuses to guess it")
