"""D-200 — ONE-SHOT DOGS: fire exactly once, then remove yourself.

THE DEFECT THIS COVERS
----------------------
A watchdog whose readiness condition encodes a DEADLINE rather than an EDGE is
permanently true once the deadline passes, so a persistent dog on one re-fires
every interval, forever. `d181-population-bar` did exactly that on this
machine on 2026-08-30: `READY=yes WHY=24h deadline reached` stayed true, and
the dog woke its owner every 15 minutes with an identical verdict until the
owner removed it by hand. `once: true` is the fix.

WHY THE CHECKS ARE SHAPED THE WAY THEY ARE
------------------------------------------
Two failure shapes make a naive suite here green and worthless, and both are
named in the brief that commissioned this:

1. "It removed itself" is unfalsifiable unless the instrument is shown able to
   see a dog that did NOT remove itself. Every removal assertion below is run
   against a PERSISTENT twin created in the same org, fired the same way, in
   the same tick, which must still be there afterwards. If the twin also
   vanishes, the check is measuring the harness and not the feature.

2. "It fired exactly once" is satisfied by a dog that never fires at all —
   zero is not one, but a count that can only ever be 0 or 1 cannot tell them
   apart. So the persistent twin is the WITNESS THAT TWO WAS REACHABLE: it is
   fired the same number of times against the same permanently-true condition
   and must reach 2. Only against that witness does the one-shot's 1 mean
   anything.

Run:  python tests/test_watchdog_oneshot.py
"""

import os
import subprocess
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ⚠ BEFORE the first orgtree import — `store` resolves ORGTREE_DATA at import
# time, so a root set afterwards leaves an env var that says "isolated" and a
# module pointed at production.
os.environ["ORGTREE_DATA"] = tempfile.mkdtemp(prefix="orgtree-wd1shot-")
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w",
          encoding="utf-8") as _f:
    # the discard port: nothing here may reach the operator's real mail hub
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

import _no_deploy                                                # noqa: E402
from orgtree import store, supervisor                            # noqa: E402
from orgtree.ledger import USER, LedgerError                     # noqa: E402

# ☠ both halves of "no test may touch production". The engine this suite
# exercises RUNS armed dogs and WAKES their owners — against the live root
# that is real spawns, real mail and real billed turns.
_no_deploy.install()
_no_deploy.assert_isolated_data_root()

# ⚠ …and confirm WHICH orgtree we are testing. A suite run from a worktree
# while PYTHONPATH points at main imports MAIN and reports confident numbers
# about the wrong code.
_HERE = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
_GOT = os.path.realpath(os.path.dirname(os.path.dirname(supervisor.__file__)))
if _GOT != _HERE:
    raise SystemExit(
        f"☠ REFUSING TO RUN: this suite lives under {_HERE!r} but imported "
        f"orgtree from {_GOT!r}. Every number it printed would be about a "
        f"different checkout. Clear PYTHONPATH and run it again.")
print(f"testing orgtree at: {_GOT}")

PASS = 0
FAIL: list[str] = []
WIN = os.name == "nt"


def check(label, fn):
    global PASS
    try:
        fn()
    except Exception as e:                                       # noqa: BLE001
        FAIL.append(f"{label}: {e}")
        print(f"  FAIL  {label}\n        {e}")
        return
    PASS += 1
    print(f"  ok    {label}")


def fixture(name):
    """An org with one live bash-holding agent `k`."""
    o = store.create_org(name)
    o.hire(USER, None, "haiku", 5, "k", add_dirs=[],
           tools={"bash": True, "web": False, "edit": False,
                  "subagents": False, "mcp": []},
           org_visibility="team", charter="one-shot watchdog fixture")
    return o


def mail_count(o, owner="k"):
    return len((o.d.get("mail") or {}).get(owner) or [])


def exists(o, wid):
    try:
        o._watchdog(wid)
        return True
    except LedgerError:
        return False


# ---------------------------------------------------------------------------
print("\n§0 · the instrument itself — can it see a dog that did NOT vanish?")
# The whole suite reports absence. An absence-detector that cannot also
# report PRESENCE proves nothing, so prove it here before using it.


def _absence_detector_can_see_both_answers():
    o = fixture("zz 1shot detector")
    try:
        w = o.watchdog_create("k", "plain", "command", "echo hi", "hi", 15)
        assert exists(o, w["id"]), \
            "the detector says a freshly-created dog does not exist"
        o.watchdog_action("k", w["id"], "remove")
        assert not exists(o, w["id"]), (
            "THE DETECTOR IS A FICTION: it reported a removed dog as still "
            "present, so every 'it removed itself' check below is worthless")
    finally:
        store.delete_org(o.d["slug"])


check("the presence/absence detector answers BOTH ways",
      _absence_detector_can_see_both_answers)


# ---------------------------------------------------------------------------
print("\n§1 · the flag itself — default OFF, no migration")


def _default_is_off_and_sparse_on_disk():
    o = fixture("zz 1shot default")
    try:
        r = o.watchdog_create("k", "plain", "command", "echo hi", "hi", 15)
        w = o._watchdog(r["id"])
        assert "once" not in w, (
            "a dog created WITHOUT the flag carries a `once` key on disk — "
            f"that is a migration nobody asked for: {w!r}")
        assert r["once"] is False, f"create claimed once={r['once']!r}"
        r2 = o.watchdog_create("k", "shot", "command", "echo hi", "hi", 15,
                               False, None, True)
        assert o._watchdog(r2["id"]).get("once") is True, \
            "once=True was not persisted"
        assert r2["once"] is True, f"create claimed once={r2['once']!r}"
        assert "ONE-SHOT" in r2["status"], \
            f"the create receipt does not say it is one-shot: {r2['status']!r}"
        assert "ONE-SHOT" not in r["status"], (
            "a PERSISTENT dog's receipt claims it is one-shot — the wording "
            f"is not actually keyed on the flag: {r['status']!r}")
    finally:
        store.delete_org(o.d["slug"])


check("default OFF, stored sparsely, and the receipt says which it is "
      "(control: persistent receipt must NOT say one-shot)",
      _default_is_off_and_sparse_on_disk)


def _once_is_a_real_boolean_in_both_projections():
    """styling builds against this and was told never to infer one-shot-ness
    from anything else, so `once` must be a BOOLEAN — never absent — in both
    payloads the frontend can reach."""
    o = fixture("zz 1shot projection")
    try:
        p = o.watchdog_create("k", "plain", "command", "echo hi", "hi", 15)
        s = o.watchdog_create("k", "shot", "command", "echo hi", "hi", 15,
                              False, None, True)
        rows = {w["id"]: w for w in o.tree()["watchdogs"]}
        assert rows[p["id"]]["once"] is False, (
            "tree() gave a persistent dog once=%r — a UI cannot render a "
            "badge off that" % (rows[p["id"]].get("once"),))
        assert rows[s["id"]]["once"] is True, \
            f"tree() gave a one-shot dog once={rows[s['id']].get('once')!r}"
        lp = supervisor.wd_list_row(o._watchdog(p["id"]))
        ls = supervisor.wd_list_row(o._watchdog(s["id"]))
        assert lp["once"] is False and ls["once"] is True, (
            f"wd_list_row disagrees with tree(): persistent={lp.get('once')!r} "
            f"one-shot={ls.get('once')!r}")
    finally:
        store.delete_org(o.d["slug"])


check("`once` is a real boolean in tree() AND wd_list_row (control: the "
      "persistent twin must read False, not missing)",
      _once_is_a_real_boolean_in_both_projections)


# ---------------------------------------------------------------------------
print("\n§2 · fire once and vanish — against a witness that TWO was reachable")


def _one_shot_fires_once_persistent_fires_twice():
    """THE CENTRAL CHECK.

    Both dogs are fired TWICE against the same permanently-true condition —
    the deadline shape that caused this feature to exist. The persistent twin
    is not decoration: it is the witness that a second fire was reachable at
    all. Without it, "the one-shot produced one mail" is equally consistent
    with a one-shot that fires once and a feature that broke firing entirely.
    """
    o = fixture("zz 1shot central")
    try:
        persistent = o.watchdog_create("k", "keeps", "command",
                                       "echo READY", "READY", 15)
        shot = o.watchdog_create("k", "spends", "command",
                                 "echo READY", "READY", 15,
                                 False, None, True)
        before = mail_count(o)

        # ── fire 1: both must deliver
        assert o.watchdog_fire(persistent["id"], "READY", "body") == "k"
        assert o.watchdog_fire(shot["id"], "READY", "body") == "k"
        assert mail_count(o) == before + 2, \
            f"first fire did not deliver two mails: {mail_count(o) - before}"

        # ── fire 2: the persistent dog is STILL THERE and fires again
        assert exists(o, persistent["id"]), \
            "the persistent twin vanished — the harness, not the feature"
        assert o.watchdog_fire(persistent["id"], "READY", "body") == "k", \
            "the persistent twin refused a second fire"
        assert mail_count(o) == before + 3, (
            "THE WITNESS FAILED: the persistent dog could not reach a second "
            "mail, so 'the one-shot only sent one' proves nothing at all")

        # ── …and the one-shot is gone, so a second fire is not even possible
        assert not exists(o, shot["id"]), (
            "the one-shot dog is STILL IN THE REGISTRY after firing — this is "
            "the runaway D-200 exists to stop")
        try:
            o.watchdog_fire(shot["id"], "READY", "body")
            raise AssertionError("firing the spent one-shot dog SUCCEEDED — "
                                 "it is still live somewhere")
        except LedgerError:
            pass
        assert mail_count(o) == before + 3, \
            "the spent one-shot dog delivered a second mail"

        # counts, stated as the brief asked: 1 against a reachable 2
        assert int(o._watchdog(persistent["id"]).get("fired")) == 2
    finally:
        store.delete_org(o.d["slug"])


check("one-shot fires ONCE while its persistent twin reaches TWO on the same "
      "permanently-true condition (the witness)",
      _one_shot_fires_once_persistent_fires_twice)


def _the_mail_says_it_removed_itself():
    o = fixture("zz 1shot mailtext")
    try:
        p = o.watchdog_create("k", "keeps", "command", "echo R", "R", 15)
        s = o.watchdog_create("k", "spends", "command", "echo R", "R", 15,
                              False, None, True)
        o.watchdog_fire(p["id"], "R", "the event body")
        o.watchdog_fire(s["id"], "R", "the event body")
        box = o.d["mail"]["k"]
        pm, sm = box[-2]["body"], box[-1]["body"]
        assert "REMOVED ITSELF" in sm, (
            "the one-shot's fire mail does not say it removed itself — the "
            f"owner will call `list`, not find it, and wonder: {sm!r}")
        assert "REMOVED ITSELF" not in pm, (
            "the PERSISTENT dog's mail also claims it removed itself, so the "
            f"sentence is unconditional and tells the owner nothing: {pm!r}")
    finally:
        store.delete_org(o.d["slug"])


check("the fire mail explains the disappearance (control: persistent mail "
      "must NOT carry the sentence)", _the_mail_says_it_removed_itself)


def _the_note_survives_a_maximum_length_body():
    """A one-shot dog whose event body is already at the 8000-char ceiling
    must still be TOLD it removed itself — appending and then truncating
    would drop exactly the sentence that explains the absence."""
    o = fixture("zz 1shot longbody")
    try:
        s = o.watchdog_create("k", "spends", "command", "echo R", "R", 15,
                              False, None, True)
        o.watchdog_fire(s["id"], "R", "X" * 12000)
        body = o.d["mail"]["k"][-1]["body"]
        assert len(body) <= 8000, f"mail body exceeded the cap: {len(body)}"
        assert "REMOVED ITSELF" in body, (
            "a maximum-length event pushed the self-removal sentence off the "
            "end of the mail — the one case where the owner most needs it")
    finally:
        store.delete_org(o.d["slug"])


check("the self-removal sentence survives an over-long event body",
      _the_note_survives_a_maximum_length_body)


def _removal_is_durable_not_just_in_memory():
    """Restart survival: dogs are re-armed from the document at startup, so a
    spent one-shot that is only gone in memory comes back armed."""
    o = fixture("zz 1shot durable")
    slug = o.d["slug"]
    try:
        p = o.watchdog_create("k", "keeps", "command", "echo R", "R", 15)
        s = o.watchdog_create("k", "spends", "command", "echo R", "R", 15,
                              False, None, True)
        o.watchdog_fire(p["id"], "R", "b")
        o.watchdog_fire(s["id"], "R", "b")
        store.save_org(o)
        fresh = store.load_org(slug)          # the persistence path, reloaded
        assert exists(fresh, p["id"]), (
            "the persistent twin did not survive the save/load — this check "
            "is testing the store, not the feature")
        assert not exists(fresh, s["id"]), (
            "THE SPENT ONE-SHOT CAME BACK: it is gone from memory but present "
            "on disk, so the next backend restart re-arms it")
        assert len((fresh.d.get("mail") or {}).get("k") or []) == 2, \
            "the mail did not survive alongside the removal"
    finally:
        store.delete_org(slug)


check("removal is persisted, so a restart cannot re-arm a spent dog "
      "(control: the persistent twin survives the same round-trip)",
      _removal_is_durable_not_just_in_memory)


# ---------------------------------------------------------------------------
print("\n§2b · the fire must still be DRAWABLE after the dog is gone")
# User catch, 2026-08-30: the canvas animates a fire as a spark from the dog
# to its owner. `OrgCanvas.launchSpark` takes dog positions from
# `tree().watchdogs` and SILENTLY draws nothing when an endpoint is unplaced,
# so a dog that erased itself atomically with its fire deleted its own origin
# and the user saw mail appear from nowhere.
#
# The backend half of that contract is exactly: at fire time and for the
# tombstone TTL after it, the id the spark is emitted FROM must still resolve
# in `tree().watchdogs`. That is what these check.


def _spark_origin_resolves_for_a_spent_one_shot(ttl_override=None):
    """Returns (spark_from_id, resolvable). Shared by the check and its
    mutant so both are asking the identical question of the identical code."""
    o = fixture("zz 1shot spark")
    slug = o.d["slug"]
    old_ttl = type(o).WATCHDOG_TOMB_TTL_S
    try:
        if ttl_override is not None:
            type(o).WATCHDOG_TOMB_TTL_S = ttl_override
        s = o.watchdog_create("k", "spends", "command", "echo R", "R", 15,
                              False, None, True)
        o.watchdog_fire(s["id"], "R", "b")
        store.save_org(o)
        # the id `_wd_fire` passes to mail_spark for this dog, verbatim
        spark_from = "dog:" + s["id"]
        placed = {"dog:" + str(w["id"])
                  for w in store.load_org(slug).tree()["watchdogs"]}
        return spark_from, (spark_from in placed)
    finally:
        type(o).WATCHDOG_TOMB_TTL_S = old_ttl
        try:
            store.delete_org(slug)
        except Exception:                                      # noqa: BLE001
            pass


def _the_spark_can_be_drawn():
    src, ok = _spark_origin_resolves_for_a_spent_one_shot()
    assert ok, (
        f"THE FIRE IS INVISIBLE: the spark is emitted from {src!r} but that "
        "id has no entry in tree().watchdogs, so launchSpark's placed() "
        "guard drops it and the user sees mail arrive from nowhere")

    # ☠ THE MUTANT, and it is the whole reason this check is trustworthy.
    # A PERSISTENT dog resolves its spark origin no matter how this feature
    # is built, so testing one proves nothing. Instead: take the SAME
    # one-shot path and set the tombstone TTL to 0 — a VALUE replacement, not
    # a deleted call — which is precisely the "dog gone before the spark can
    # start" state the user described. The assertion above must fail on it.
    src2, ok2 = _spark_origin_resolves_for_a_spent_one_shot(ttl_override=0)
    assert not ok2, (
        "MUTANT SURVIVED: with the tombstone TTL set to 0 the spark origin "
        f"{src2!r} STILL resolved, so the check above cannot detect the very "
        "regression it exists for and its pass means nothing")


check("the spark's origin id still resolves after a one-shot dog is spent "
      "(mutant: TTL=0 must break it)", _the_spark_can_be_drawn)


def _a_tombstone_is_inert_and_temporary():
    """It must not be mistakable for a live dog: not armed, not listable, not
    counted against the cap, and gone once its TTL passes."""
    o = fixture("zz 1shot tomb inert")
    slug = o.d["slug"]
    try:
        s = o.watchdog_create("k", "spends", "command", "echo R", "R", 15,
                              False, None, True)
        o.watchdog_fire(s["id"], "R", "b")
        store.save_org(o)
        fresh = store.load_org(slug)
        rows = {w["id"]: w for w in fresh.tree()["watchdogs"]}
        assert s["id"] in rows, "the tombstone is not in the tree payload"
        t = rows[s["id"]]
        assert t["spent"] is True and t["state"] == "spent", (
            "a tombstone does not declare itself spent, so the canvas will "
            f"draw it as a live dog: {t!r}")
        assert t["once"] is True, "a tombstone lost its one-shot identity"
        # …and every live dog says spent=False, so the field is a real
        # discriminator rather than a marker that only ever appears once
        live = fresh.watchdog_create("k", "alive", "command", "echo R", "R", 15)
        assert next(w for w in fresh.tree()["watchdogs"]
                    if w["id"] == live["id"])["spent"] is False, \
            "a LIVE dog reports spent=True — the flag distinguishes nothing"
        # invisible to the agent-facing surfaces
        assert not exists(fresh, s["id"]), \
            "a tombstone is reachable as a real watchdog"
        assert s["id"] not in [w["id"] for w in
                               (fresh.d.get("watchdogs") or [])], \
            "a tombstone was written into the arming registry"
        # …and it expires
        type(fresh).WATCHDOG_TOMB_TTL_S = 0
        try:
            assert s["id"] not in [w["id"] for w in fresh.tree()["watchdogs"]], \
                "an expired tombstone is still rendered — it never goes away"
        finally:
            type(fresh).WATCHDOG_TOMB_TTL_S = 15
    finally:
        store.delete_org(slug)


check("a tombstone is inert, self-declaring and expires (control: a live dog "
      "must report spent=False)", _a_tombstone_is_inert_and_temporary)


def _a_tombstone_frees_the_per_agent_slot():
    o = fixture("zz 1shot cap")
    try:
        ids = [o.watchdog_create("k", f"d{i}", "command", "echo R", "R", 15,
                                 False, None, i == 0)["id"]
               for i in range(o.WATCHDOG_PER_AGENT)]
        try:
            o.watchdog_create("k", "extra", "command", "echo R", "R", 15)
            raise AssertionError("the per-agent cap did not bite at all — "
                                 "this check cannot detect a leak")
        except LedgerError:
            pass
        o.watchdog_fire(ids[0], "R", "b")          # the one-shot spends itself
        o.watchdog_create("k", "extra", "command", "echo R", "R", 15)
    finally:
        store.delete_org(o.d["slug"])


check("a spent one-shot dog gives its per-agent slot back (control: the cap "
      "must refuse before it fires)", _a_tombstone_frees_the_per_agent_slot)


# ---------------------------------------------------------------------------
print("\n§3 · what must NOT spend a one-shot dog")


def _an_alert_does_not_spend_it():
    """`watchdog_alert` means 'I can no longer answer your question'. Retiring
    the dog on that would throw the watch away precisely when it has NOT been
    answered."""
    o = fixture("zz 1shot alert")
    try:
        s = o.watchdog_create("k", "spends", "command", "echo R", "R", 15,
                              False, None, True)
        o.watchdog_alert(s["id"], "your target went quiet")
        assert exists(o, s["id"]), (
            "a QUIET-SUBJECT alert removed a one-shot dog — the owner is now "
            "not being watched and was told the opposite")
        assert int(o._watchdog(s["id"]).get("fired") or 0) == 0, \
            "an alert incremented the fire counter"
        # …and it is still able to do its actual job afterwards
        assert o.watchdog_fire(s["id"], "R", "b") == "k"
        assert not exists(o, s["id"]), \
            "after an alert, the real fire no longer spends the dog"
    finally:
        store.delete_org(o.d["slug"])


check("an alert does NOT spend a one-shot dog, and the later real fire still "
      "does", _an_alert_does_not_spend_it)


def _a_refused_fire_does_not_spend_it():
    """A fire that delivers nothing must not consume the dog. Two refusal
    paths exist: a paused dog, and an archived owner."""
    o = fixture("zz 1shot refused")
    try:
        s = o.watchdog_create("k", "spends", "command", "echo R", "R", 15,
                              False, None, True)
        o.watchdog_action("k", s["id"], "pause")
        assert o.watchdog_fire(s["id"], "R", "b") is None
        assert exists(o, s["id"]), \
            "a PAUSED one-shot dog was consumed by a fire that never mailed"
        o.watchdog_action("k", s["id"], "resume")

        o.retire(USER, "k")
        assert o.watchdog_fire(s["id"], "R", "b") is None
        assert exists(o, s["id"]), (
            "an ARCHIVED owner's one-shot dog was consumed by a fire that "
            "delivered nothing — the event is lost with no trace, which is "
            "the worse half of the failure this feature had to avoid")
        assert o._watchdog(s["id"])["state"] == "paused", \
            "the archive-pause did not happen"
    finally:
        store.delete_org(o.d["slug"])


check("a fire that delivers NOTHING (paused dog / archived owner) does not "
      "spend a one-shot dog", _a_refused_fire_does_not_spend_it)


# ---------------------------------------------------------------------------
print("\n§4 · the passive path — a one-shot NOTICE dog must stay passive")
# The regression this guards is one the implementation genuinely had: _wd_fire
# used to read `notice` AFTER the fire, and a one-shot dog is gone by then, so
# the lookup failed, notice fell back to False, and a dog armed to be silent
# woke its owner instead.


def _one_shot_notice_dog_fires_passively():
    from concurrent.futures import ThreadPoolExecutor          # noqa: PLC0415
    _no_deploy.install_no_turn_spawn()
    _no_deploy.WAKES.clear()
    o = fixture("zz 1shot notice")
    slug = o.d["slug"]
    real_pool = supervisor._wd_cmd_pool
    try:
        # identical dogs but for `notice`; both one-shot
        quiet = o.watchdog_create("k", "quiet", "command", "echo READY",
                                  "READY", 15, True, None, True)
        loud = o.watchdog_create("k", "loud", "command", "echo READY",
                                 "READY", 15, False, None, True)
        store.save_org(o)
        supervisor._wd_cmd_pool = ThreadPoolExecutor(max_workers=2)
        supervisor._wd_tick()
        deadline = time.time() + 90
        while time.time() < deadline:
            fresh = store.load_org(slug)
            if not exists(fresh, quiet["id"]) and not exists(fresh,
                                                             loud["id"]):
                break
            time.sleep(0.5)
        fresh = store.load_org(slug)
        assert not exists(fresh, quiet["id"]), \
            "the one-shot NOTICE dog did not remove itself through the engine"
        assert not exists(fresh, loud["id"]), \
            "the one-shot WAKING dog did not remove itself through the engine"
        wakes = [w for w in _no_deploy.WAKES if w[1] == "k"]
        assert len(wakes) >= 2, (
            f"expected a send for each dog, saw {len(wakes)}: {wakes!r}")
        woke = [w for w in wakes if w[3]]
        assert len(woke) == 1, (
            "EXACTLY ONE of the two one-shot dogs should have started a turn "
            f"(the non-notice one). Woke={len(woke)} of {len(wakes)}. If this "
            f"is 2, a one-shot NOTICE dog is waking its owner: {wakes!r}")
    finally:
        try:
            supervisor._wd_cmd_pool.shutdown(wait=True)
        except Exception:                                      # noqa: BLE001
            pass
        supervisor._wd_cmd_pool = real_pool
        supervisor.send_message = _no_deploy._REAL_SEND_MESSAGE
        try:
            store.delete_org(slug)
        except Exception:                                      # noqa: BLE001
            pass


check("E2E: a one-shot NOTICE dog stays PASSIVE while its one-shot waking "
      "twin starts a turn (control pair on the same tick)",
      _one_shot_notice_dog_fires_passively)


# ---------------------------------------------------------------------------
print("\n§5 · the engine end to end — the actual runaway, reproduced")


def _real_ticks_persistent_repeats_one_shot_does_not():
    """The `d181-population-bar` shape, run for real: a command whose output
    is permanently matching, checked on two consecutive intervals.

    The persistent dog must fire on BOTH — that is the runaway, and it is also
    the witness that a second fire was reachable through the ENGINE and not
    merely through a direct ledger call. The one-shot must fire on the first
    and be gone for the second.
    """
    from concurrent.futures import ThreadPoolExecutor          # noqa: PLC0415
    _no_deploy.install_no_turn_spawn()
    _no_deploy.WAKES.clear()
    o = fixture("zz 1shot engine")
    slug = o.d["slug"]
    d = tempfile.mkdtemp(prefix="wd1shot-e2e-")
    marker = os.path.join(d, "state.txt")
    with open(marker, "w", encoding="utf-8") as fh:
        fh.write("READY=yes WHY=24h deadline reached\n")
    tgt = (f'findstr /C:"READY=yes" "{marker}"' if WIN
           else f'grep -F "READY=yes" "{marker}"')
    real_pool = supervisor._wd_cmd_pool
    try:
        keeps = o.watchdog_create("k", "keeps", "command", tgt,
                                  "READY=yes", 15)
        spends = o.watchdog_create("k", "spends", "command", tgt,
                                   "READY=yes", 15, False, None, True)
        store.save_org(o)
        supervisor._wd_cmd_pool = ThreadPoolExecutor(max_workers=2)

        def settle(pred, why, timeout=90):
            end = time.time() + timeout
            while time.time() < end:
                if pred(store.load_org(slug)):
                    return store.load_org(slug)
                time.sleep(0.5)
            raise AssertionError(why + f" (waited {timeout}s)")

        # ── interval 1
        supervisor._wd_tick()
        fresh = settle(
            lambda f: (int(f._watchdog(keeps["id"]).get("fired") or 0) >= 1
                       and not exists(f, spends["id"])),
            "after one real tick the persistent dog had not fired or the "
            "one-shot had not removed itself")
        assert int(fresh._watchdog(keeps["id"]).get("fired")) == 1

        # ── interval 2. The engine gates a check on `_last_check_ts`; clearing
        #    it is how this suite simulates the interval elapsing without
        #    sleeping 15s. It is the ONLY thing reset — the condition, the
        #    target and both dogs are untouched.
        fresh._watchdog(keeps["id"]).pop("_last_check_ts", None)
        store.save_org(fresh)
        supervisor._wd_tick()
        fresh = settle(
            lambda f: int(f._watchdog(keeps["id"]).get("fired") or 0) >= 2,
            "THE WITNESS FAILED: the persistent dog did not fire a second "
            "time on a still-true condition, so this suite cannot claim the "
            "one-shot's single fire means anything")
        assert not exists(fresh, spends["id"]), \
            "the one-shot dog reappeared on the second tick"
        assert int(fresh._watchdog(keeps["id"]).get("fired")) == 2, (
            "the persistent dog's count is not 2 — the runaway is not being "
            "reproduced and the comparison is empty")
    finally:
        try:
            supervisor._wd_cmd_pool.shutdown(wait=True)
        except Exception:                                      # noqa: BLE001
            pass
        supervisor._wd_cmd_pool = real_pool
        supervisor.send_message = _no_deploy._REAL_SEND_MESSAGE
        try:
            store.delete_org(slug)
        except Exception:                                      # noqa: BLE001
            pass


check("E2E: two real ticks — the persistent dog fires TWICE on a "
      "permanently-true condition (the runaway), the one-shot fires once and "
      "is gone", _real_ticks_persistent_repeats_one_shot_does_not)


def _a_spent_one_shot_stream_leaves_no_child_running():
    """A stream dog's target is a persistent LISTENER — the longest-lived
    child this subsystem makes. "Removes itself" has to mean the process too,
    or a one-shot stream dog leaks a listener every time it is used.

    ⚠ THIS CHECK DELIBERATELY DOES NOT USE `_wd_tick`, and the first version
    of it did. `_wd_tick` ends with a sweep that reaps any stream whose dog
    has vanished from the document, so a suite that drives whole ticks sees a
    dead listener whether or not the fire reaped it — the reap and the sweep
    are indistinguishable from outside. Mutation testing caught exactly that:
    disabling the fire-path reap left this check green.

    So the fire is driven through `_wd_ensure_stream` ALONE. Nothing else runs
    between the fire and the assertions, which makes the question precise:
    did the FIRE tear the listener down, or is something else cleaning up
    afterwards? Only the first satisfies "removes itself as part of the fire".
    """
    _no_deploy.install_no_turn_spawn()
    _no_deploy.WAKES.clear()
    o = fixture("zz 1shot stream")
    slug = o.d["slug"]
    # print a matching line, then stay alive doing nothing for a long time
    tgt = ('cmd /c "echo HIT& ping -n 120 127.0.0.1 >nul"' if WIN
           else 'sh -c "echo HIT; sleep 120"')
    proc = None
    try:
        s = o.watchdog_create("k", "listener", "stream", tgt, "HIT", 5,
                              False, None, True)
        store.save_org(o)
        key = (slug, s["id"])
        w = store.load_org(slug)._watchdog(s["id"])

        # ① spawn the listener (this call only starts it)
        supervisor._wd_ensure_stream(slug, store.load_org(slug), w, key)
        end = time.time() + 30
        while time.time() < end:
            with supervisor._wd_lock:
                ent = supervisor._wd_streams.get(key)
            if ent is not None and ent["buf"]:
                proc = ent["proc"]
                break
            time.sleep(0.2)
        assert proc is not None, (
            "the stream dog never spawned a child that produced a matching "
            "line — this check cannot test teardown")
        assert proc.poll() is None, \
            "the listener exited on its own — it cannot test teardown"

        # ② the fire, and NOTHING else. No tick, so no sweep to hide behind.
        supervisor._wd_ensure_stream(slug, store.load_org(slug), w, key)

        assert not exists(store.load_org(slug), s["id"]), \
            "the one-shot STREAM dog did not remove itself when it fired"
        with supervisor._wd_lock:
            leaked = supervisor._wd_streams.get(key)
        assert leaked is None, (
            "the spent one-shot stream dog is gone from the document but its "
            "entry is still in the engine's stream table — the listener is "
            "orphaned and only a later tick would notice")
        end = time.time() + 20
        while time.time() < end and proc.poll() is None:
            time.sleep(0.5)
        assert proc.poll() is not None, (
            "THE LISTENER IS STILL RUNNING after its one-shot dog fired and "
            "removed itself — the fire does not tear down its own child, so "
            "this feature leaks a process on every use")
    finally:
        try:
            supervisor._wd_reap_stream((slug, s["id"]))
        except Exception:                                      # noqa: BLE001
            pass
        if proc is not None and proc.poll() is None:
            try:
                supervisor._wd_kill_tree(proc)
            except Exception:                                  # noqa: BLE001
                pass
        supervisor.send_message = _no_deploy._REAL_SEND_MESSAGE
        try:
            store.delete_org(slug)
        except Exception:                                      # noqa: BLE001
            pass


check("E2E: a spent one-shot STREAM dog tears down its listening child",
      _a_spent_one_shot_stream_leaves_no_child_running)


# ---------------------------------------------------------------------------
print(f"\n{PASS} passed, {len(FAIL)} failed")
if FAIL:
    for f in FAIL:
        print("  ✗ " + f)
    sys.exit(1)
