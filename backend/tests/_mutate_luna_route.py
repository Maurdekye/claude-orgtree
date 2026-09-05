"""Mutation harness for test_luna_reserve_route.py (item 12).

A routing rule that cannot be shown to fail is decoration. Each mutation
below is a VALUE REPLACEMENT in the shipped code — never a deleted call,
never a raised exception — and a NAMED check in the suite must go red for
it. Two controls make the rest mean anything:

  · a NOOP (one comment word) must SURVIVE. If it dies, the suite is
    environment-sensitive and every "killed" below is noise.
  · a SANITY mutant (every luna routes to a nonsense model) must DIE. If it
    survives, the suite is not running the code under test at all.

⚠ Rewrites files IN PLACE and restores them byte-for-byte afterwards (read
and written in binary, so the repo's CRLF endings are preserved — the
older harnesses wrote LF back and left the tree permanently dirty). Run it
only inside a worktree.

Run:  python tests/_mutate_luna_route.py
"""
import os
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
ROUTE = os.path.join(BACKEND, "orgtree", "codex_route.py")
SUP = os.path.join(BACKEND, "orgtree", "supervisor.py")
API = os.path.join(BACKEND, "orgtree", "api.py")
LEDGER = os.path.join(BACKEND, "orgtree", "ledger.py")
LIMITS = os.path.join(BACKEND, "orgtree", "codex_limits.py")
SUITE = os.path.join(HERE, "test_luna_reserve_route.py")

# (label, file, find, replace, what it breaks)
MUTANTS = [
    ("NOOP CONTROL — one comment word changed",
     ROUTE,
     "#: pool names — the RESOURCE a turn spends, distinct from the model string.",
     "#: pool names — the RESOURCE a turn spends, distinct from the model id.",
     "MUST SURVIVE — nothing about behaviour changed"),

    ("SANITY CONTROL — every reserve route names a nonsense model",
     ROUTE,
     '                "model": RESERVE_MODEL, "account": account, "reason": reason,\n',
     '                "model": "banana", "account": account, "reason": reason,  # MUTANT\n',
     "MUST DIE — proves the suite runs the code under test"),

    ("M1 — a rejection no longer needs the turn to have run NOTHING",
     ROUTE,
     '    rejected = (status == "failed" and kind == KIND_USAGE_LIMIT\n'
     "                and nothing_ran)\n",
     '    rejected = (status == "failed" and kind == KIND_USAGE_LIMIT  # MUTANT\n'
     "                and True)\n",
     "a wall hit after output is replayed on the other pool (§4, §10)"),

    ("M2 — absence on a SPARSE board is read as a withdrawn grant",
     ROUTE,
     "        elif (cap[\"state\"] == \"absent\" and pool == RESERVE_POOL\n"
     "                and bool(board.get(\"complete\"))):\n",
     "        elif (cap[\"state\"] == \"absent\" and pool == RESERVE_POOL\n"
     "                and True):  # MUTANT\n",
     "a turn's sparse notification that omits reserve pins luna to direct (§1)"),

    ("M3 — a mark from ANOTHER account binds this one",
     ROUTE,
     '    if str(m.get("account") or "") != account:\n'
     "        return False\n",
     '    if False and str(m.get("account") or "") != account:  # MUTANT\n'
     "        return False\n",
     "yesterday's withdrawal on a different login pins today's luna (§1)"),

    ("M4 — the checkbox is ignored: always reserve first",
     ROUTE,
     "    order = ((RESERVE_POOL, PLAN_POOL) if prefer_reserve\n"
     "             else (PLAN_POOL, RESERVE_POOL))\n",
     "    order = (RESERVE_POOL, PLAN_POOL)  # MUTANT\n",
     "an unticked luna still spends reserve first (§2, §8)"),

    ("M5 — the token forgets to say LAST",
     ROUTE,
     '    prefix = "" if live else "last: "\n',
     '    prefix = ""  # MUTANT\n',
     "a stale route reads as a live one on the header (§5, §12)"),

    ("M6 — a stream disconnect is classified as a usage limit",
     ROUTE,
     '    "responsestreamdisconnected": KIND_CONNECTION,\n',
     '    "responsestreamdisconnected": KIND_USAGE_LIMIT,  # MUTANT\n',
     "an unknown outcome is replayed on the other pool (§4, §10)"),

    ("M7 — a pool's reset is the EARLIEST of its exhausted windows",
     ROUTE,
     '            "reset_ts": (max(known) if known and not unknown else None),\n',
     '            "reset_ts": (min(known) if known and not unknown else None),  # MUTANT\n',
     "a session window's reset releases a pool whose weekly is still spent (§3)"),

    ("M8 — the rejection mark is never written",
     SUP,
     "        _codex_route_persist(slug, nid, rec, mark=(rj.route[\"pool\"], mark))\n",
     "        _codex_route_persist(slug, nid, rec)  # MUTANT\n",
     "the next turn re-asks the rejected pool when the board is stale (§7, §8, §9)"),

    ("M9 — the retry writes the user's row a second time",
     SUP,
     '        "sid": journal_sid or "", "barrier": not journal_sid,\n',
     '        "sid": "", "barrier": True,  # MUTANT\n',
     "the prompt stands in the transcript twice after a re-drive (§7)"),

    ("M10 — the cache namespace hashes the tier's model, not the route's",
     SUP,
     '        "model": route_model or claude_model_for(org, nid),\n',
     '        "model": claude_model_for(org, nid),  # MUTANT\n',
     "a reserve→direct flip is invisible to the forecast (§6)"),

    ("M11 — the tree's token is live whenever the record says so",
     API,
     '            _live = bool(_rt.get("live")) and bool(st.get("busy"))\n',
     '            _live = bool(_rt.get("live"))  # MUTANT\n',
     "the source pin for the live rule (§12)"),

    ("M12 — the hire door accepts a new gpt-reserve again",
     API,
     "    if tier in providers.LEGACY_CODEX_TIERS:\n",
     "    if False and tier in providers.LEGACY_CODEX_TIERS:  # MUTANT\n",
     "the retired token is hireable through the API (§11, §12)"),

    ("M13 — an absent preference reads as plan-first",
     LEDGER,
     "        return True if v is None else bool(v)\n",
     "        return False if v is None else bool(v)  # MUTANT\n",
     "every existing luna silently flips to weekly-first (§12, §6)"),

    ("M14 — a completed turn no longer clears its pool's mark",
     SUP,
     '    _codex_route_persist(slug, nid, _rec, clear_mark=route["pool"])\n',
     '    _codex_route_persist(slug, nid, _rec)  # MUTANT\n',
     "positive recovery never clears a stale rejection (§7 next-turn / §6)"),

    ("M15 — an OLD positive observation outranks a NEWER rejection mark",
     ROUTE,
     "            if not newer:\n",
     "            if True:  # MUTANT\n",
     "a board read before the rejection re-sends the rejected pool (§1 review case)"),

    ("M16 — a usage tag on a non-terminal status counts as a rejection",
     ROUTE,
     '    rejected = (status == "failed" and kind == KIND_USAGE_LIMIT\n'
     "                and nothing_ran)\n",
     "    rejected = (kind == KIND_USAGE_LIMIT  # MUTANT\n"
     "                and nothing_ran)\n",
     "an interrupted/in-progress turn could be replayed (§4 review case)"),

    ("M17 — a board from another account is read as this account's",
     ROUTE,
     "    if fresh and board_acct is not None and str(board_acct) != account:\n",
     "    if False:  # MUTANT\n",
     "someone else's exhausted reserve routes this login direct (§1, §13)"),

    ("M18 — the cached board survives an account change",
     LIMITS,
     '        if cached is not None and _cache.get("account") not in (None, acct):\n',
     "        if False:  # MUTANT\n",
     "after `codex login` the old account's pools are served as the new one's (§13)"),

    ("M20 — an unnamed per-turn wall is filed under the PLAN whatever pool served it",
     ROUTE,
     "        snap_pool = (pool_of_snapshot(snap) if named or sent_pool is None\n"
     "                     else sent_pool)\n",
     "        snap_pool = pool_of_snapshot(snap)  # MUTANT\n",
     "a reserve wall is read as a plan wall: wrong mark, wrong wake (§4, §7)"),

    ("M21 — the shared board folds a reserve turn's notification into the plan bucket",
     LIMITS,
     '    if pool_hint == "reserve" and not str(snap.get("limitName") or "").strip():\n',
     "    if False:  # MUTANT\n",
     "after a reserve turn the plan bucket wears reserve's numbers (§4, §7 next turn)"),

    ("M22 — a server reroute is ignored: the sent pool is always the served pool",
     ROUTE,
     "    if isinstance(rerouted, dict):\n"
     '        to = str(rerouted.get("toModel") or "").strip()\n',
     "    if False:  # MUTANT\n"
     '        to = str(rerouted.get("toModel") or "").strip()\n',
     "a rerouted turn's wall is charged to the pool it was merely sent to (§4)"),

    ("M19 — a sparse notification re-observes EVERY bucket",
     LIMITS,
     "        slots = _observed.setdefault(limit_id, {})\n",
     "        for _k in _snapshots:  # MUTANT\n"
     "            _observed[_k] = {\"primary\": now, \"secondary\": now}\n"
     "        slots = _observed.setdefault(limit_id, {})\n",
     "a plan update looks like fresh reserve evidence and clears a reserve mark (§13, §14)"),

    ("M23 — a notification re-stamps a slot it did NOT carry",
     LIMITS,
     '            if isinstance(snap.get(slot), dict):\n'
     "                slots[slot] = now\n",
     "            if True:  # MUTANT\n"
     "                slots[slot] = now\n",
     "a primary-only update makes the retained secondary look fresh (§14)"),

    ("M24 — the resolver never ages a window",
     ROUTE,
     "        if age is not None and age > max_age:\n",
     "        if False:  # MUTANT\n",
     "1000-second-old reserve exhaustion stays binding under plan-only updates (§14)"),

    ("M25 — a notification from ANOTHER account merges into this board",
     LIMITS,
     "        if board_acct is not None and str(board_acct) != origin:\n",
     "        if False:  # MUTANT\n",
     "a B-login turn's reserve reading sends A's luna to reserve (§15)"),

    ("M26 — a full read whose login moved mid-read is cached anyway",
     LIMITS,
     "            if acct_before != acct_after or acct_before != acct:\n",
     "            if False:  # MUTANT\n",
     "a board stamped B carries A's numbers (§15)"),

    ("M27 — the supervisor folds under the login as of delivery, not the route's",
     SUP,
     '            if codex_limits.observe(_snap, pool_hint=_served,\n'
     '                                    account=route["account"]):\n',
     "            if codex_limits.observe(_snap, pool_hint=_served,  # MUTANT\n"
     "                                    account=None):\n",
     "the handoff loses the captured account (§15 handoff)"),

    ("M28 — the token ignores a known reroute",
     ROUTE,
     "    if isinstance(rr, dict):\n",
     "    if False:  # MUTANT\n",
     "a reserve turn the server served direct still wears 'reserve' (§16)"),

    ("M29 — a rejection after a reroute is re-driven on the pool that rejected",
     SUP,
     '        if fcls["redrive"] and other is not None:\n',
     '        if fcls["rejected"] and other is not None:  # MUTANT\n',
     "reserve→direct reroute + direct wall marks reserve and re-sends direct (§16)"),

    ("M30 — an unknown reroute destination's notification is folded as the plan's",
     SUP,
     "    if _served is not None:\n"
     "        for _snap in (list(_snaps.values()) if isinstance(_snaps, dict)\n",
     "    if True:  # MUTANT\n"
     "        for _snap in (list(_snaps.values()) if isinstance(_snaps, dict)\n",
     "an unobserved destination is inferred to be the plan (§16 unknown)"),

    ("M31 — the mid-turn reroute stamp drops the reroute",
     SUP,
     "                _codex_route_stamp(st, _r, live=True, rerouted=rr)\n",
     "                _codex_route_stamp(st, _r, live=True, rerouted=None)  # MUTANT\n",
     "row 2 keeps saying reserve while the server serves direct (§16 live stamp)"),

    ("M32 — a rejection is attributed to the pool SENT, whatever served",
     ROUTE,
     '    attributed: str | None = pool if served == "<sent>" else served\n',
     "    attributed: str | None = pool  # MUTANT\n",
     "a rerouted wall is booked against reserve and re-driven (§4, §16)"),
]

#: mutants that must SURVIVE rather than die (the noop control)
MUST_SURVIVE = {"NOOP CONTROL — one comment word changed"}
#: mutants whose kill is expected only if a check pins them; listed so a
#: survivor is reported as a GAP rather than a pass
EXPECT_KILL = {m[0] for m in MUTANTS} - MUST_SURVIVE


def run_suite():
    p = subprocess.run([sys.executable, SUITE], cwd=BACKEND,
                       capture_output=True, text=True, timeout=1800,
                       encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main():
    print("baseline (unmutated) …")
    rc, out = run_suite()
    if rc != 0:
        print(out[-4000:])
        print("BASELINE IS RED — fix the suite before mutating anything.")
        return 1
    m = re.search(r"(\d+) checks passed", out)
    print(f"  baseline: {m.group(0) if m else '?'}\n")

    bad = []
    for label, path, find, repl, why in MUTANTS:
        raw = open(path, "rb").read()
        nl = b"\r\n" if b"\r\n" in raw else b"\n"
        find_b = find.encode("utf-8").replace(b"\n", nl)
        repl_b = repl.encode("utf-8").replace(b"\n", nl)
        hits = raw.count(find_b)
        if hits == 0:
            print(f"  x {label}\n      MUTATION DID NOT APPLY — the code it "
                  f"targets has moved. This mutant tested NOTHING.")
            bad.append((label, "did not apply"))
            continue
        if hits > 1:
            print(f"  x {label}\n      AMBIGUOUS TARGET — {hits} matches in "
                  f"{os.path.basename(path)}. Widen `find` until it is unique.")
            bad.append((label, f"ambiguous target ({hits} matches)"))
            continue
        open(path, "wb").write(raw.replace(find_b, repl_b, 1))
        try:
            rc, out = run_suite()
        finally:
            open(path, "wb").write(raw)
        died = rc != 0
        failed = re.findall(r"^  FAIL     (.*)$", out, flags=re.M)
        if label in MUST_SURVIVE:
            if died:
                print(f"  x {label}\n      DIED — the suite is environment-"
                      f"sensitive; every kill below is suspect:\n"
                      + "\n".join(f"        · {f}" for f in failed[:5]))
                bad.append((label, "noop died"))
            else:
                print(f"  ✓ {label}\n      survived, as it must")
            continue
        if died:
            print(f"  ✓ {label}\n      killed by: "
                  + "; ".join(f[:70] for f in failed[:3]))
        else:
            print(f"  x {label}\n      SURVIVED — {why}: nothing in the suite "
                  f"notices.")
            bad.append((label, "survived"))
    print()
    if bad:
        for label, what in bad:
            print(f"GAP: {label} — {what}")
        print(f"{len(MUTANTS) - len(bad)}/{len(MUTANTS)} mutants behaved; "
              f"{len(bad)} gaps")
        return 1
    print(f"all {len(MUTANTS)} mutants behaved (noop survived, the rest died)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
