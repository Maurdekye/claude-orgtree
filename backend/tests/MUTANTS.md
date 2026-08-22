# Mutation record — `test_watchdog_visibility.py`

Team charter §3: *when a guard passes, ask what would make it FAIL, then go
make it fail once.* This file is the receipt for that, for the 2026-08-22
watchdog-visibility fix. Run date: **2026-08-22**, Windows 11, one machine.

An all-green harness is worthless on its own — and doubly so here, because
"green" **is** the symptom this fix exists to cure: a watchdog that ran 700
checks and matched nothing reported exactly what a healthy one reports. So
every claim the suite makes was checked by breaking the code and watching a
**named** check go red.

## Result: 20/20 mutations behaved as required

Including the two that make the other eighteen mean something:

| # | Mutation | Want | Got |
|---|---|---|---|
| — | **NOOP CONTROL** — one comment word changed | **survive** | survived |
| — | **SANITY CONTROL** — `wd_shell` returns `"banana"` | die | died |

If the NOOP had died, the suite would be environment-sensitive and every
"killed" below would be noise. If the SANITY mutant had survived, the suite
would not be running the code under test at all.

| # | Mutation | Killed |
|---|---|---|
| M1 | `_wd_run_command` throws its raw output away again (**the original defect**) | §3 raw-output check |
| M2 | `wd_health` never warns | all four §2 checks |
| M3 | `wd_health` warns about *every* dog (the noise failure mode) | all four §2 + §5 |
| M4 | `wd_output_broken` never detects a broken target | §1, §2, §3, §4 |
| M5 | `wd_output_broken` calls *everything* broken | §1, §2 — the **control halves** |
| M6 | `_wd_mark_check` stops counting checks | §3 mark-check |
| M7 | `_wd_mark_check` drops an EMPTY observation | §3 mark-check |
| M8 | `wd_smoke` never marks a target broken | §4 smoke |
| M9 | file dogs stop reporting an unreadable target | §3 file/process |
| M10 | process dogs stop warning about an unreachable DOWN edge | §3 file/process |
| M11 | the `list` projection hides `last_output` again | §5 |
| M12 | the `list` projection stops computing `health` | §5 |
| M15 | `api.py` inlines its own projection instead of the shipped one | §5 handler check |
| M13 | the tool card goes back to "runs WITH YOUR HANDS" | §6 |
| M14 | the data-root interlock stops refusing production | §0 |
| E1 | the fire path is suppressed (`if lines:` → `if False:`) | §7 E2E |
| E2 | `_wd_tick` stops routing command dogs to the pool | §7 E2E |
| E3 | the turn-spawn interlock stops recording wakes | §7 E2E |

M5 is the one that proves the **control pairs** are load-bearing: a sniffer
that calls everything broken is exactly as useless as one that calls nothing
broken, and only the negative half of each pair catches it.

E1 is the one that proves §7 is not theatre: if the E2E check could pass with
`_wd_fire` unreachable, it would not be testing a dog firing.

## The trap this suite fell into first — worth keeping

The first version of the §3 PATH check simply ran `echo hello | grep hello`
and asserted it failed. **It passed grep's output instead.** A suite inherits
the PATH of whoever launched it, and an agent's terminal carries Git's
`usr\bin` while the backend service does not. That check would have reported
"the bash idiom works fine here" while three dogs on this machine lay dead of
exactly that idiom — a *green* result asserting the opposite of the truth,
from the same family as the defect under repair.

The fix was to stop inheriting the condition and **set** it: the check now
pins `PATH` to a service-like one for the probe, and keeps two controls under
that same PATH (a shell builtin, and `findstr` — the idiom the tool card now
recommends) which must both survive.

Generalisation worth carrying: **a check that depends on ambient environment
is abstaining, not testing.** If the environment decides the outcome, the
check reports on the launcher, not the code.

## Production safety (charter §4)

`tests/_no_deploy.py` gained two interlocks this suite arms before any check:

- `assert_isolated_data_root()` — refuses to run if `store.DATA_ROOT` resolves
  to `~/orgtree`. It reads the **resolved** value, not `os.environ`, because a
  suite that sets `ORGTREE_DATA` after its first orgtree import has an env var
  saying "isolated" and a module pointed at production. Mutation-verified
  (M14) and checked in both directions by §0.
- `install_no_turn_spawn()` — the watchdog fire path calls
  `send_message(wake=True)`, which starts a real `claude -p` turn and bills it.
  §7 exercises that path deliberately, so the wake is intercepted and
  **recorded**; the check then asserts on `WAKES` (a positive marker that the
  wake was reached) rather than on the absence of a process it cannot see.
  Mutation-verified (E3).

The suite also refuses to run if it imported `orgtree` from a different
checkout than the one it lives in — the "confident numbers about the wrong
code" failure this repo has hit before.

## Reproducing

The harness lives outside the repo (it rewrites source files in place):
`<watchdog-fix scratch>/mutate.py`. It restores every file in a `finally`,
and treats an **anchor miss** as a failure rather than a pass — a mutation
that never applied has not proved anything.
