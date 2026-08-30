"""Mutation harness for test_watchdog_oneshot.py.

The suite is all-green, and an all-green suite is the symptom, not the proof.
Each mutation below is a VALUE REPLACEMENT in the shipped code — never a
deleted call or a raised exception, because a mutant that dies with a
NameError only proves the line executes, not that anything CHECKS it. Every
one must kill at least one named check; a survivor means that behaviour is
unverified and the suite is quietly lying about it.

Run:  python tests/_mutate_oneshot.py
"""
import os
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
LEDGER = os.path.join(BACKEND, "orgtree", "ledger.py")
SUP = os.path.join(BACKEND, "orgtree", "supervisor.py")
SUITE = os.path.join(HERE, "test_watchdog_oneshot.py")

# (label, file, find, replace, what it breaks)
MUTANTS = [
    ("one-shot dogs are never removed",
     LEDGER,
     '        one_shot = bool(w.get("once"))\n',
     '        one_shot = False  # MUTANT\n',
     "the dog stays armed after firing — the original runaway"),

    ("every dog is a one-shot dog",
     LEDGER,
     "        one_shot = bool(once)\n",
     "        one_shot = True  # MUTANT\n",
     "persistent dogs silently spend themselves"),

    ("`once` is not persisted",
     LEDGER,
     '                     **({"once": True} if one_shot else {}),\n',
     "                     **({} if one_shot else {}),  # MUTANT\n",
     "the flag is accepted and dropped"),

    ("tree() reports every dog as persistent",
     LEDGER,
     '            "watchdogs": [{**w, "once": bool(w.get("once")), '
     '"spent": False}\n',
     '            "watchdogs": [{**w, "once": False, "spent": False}  # MUTANT\n',
     "the UI can never distinguish one-shot from persistent"),

    ("wd_list_row reports every dog as persistent",
     SUP,
     '            "once": bool(w.get("once"))}\n',
     '            "once": False}  # MUTANT\n',
     "an owner cannot verify what it armed"),

    ("the fire mail does not mention self-removal",
     LEDGER,
     "            body = body[:8000 - len(self.WATCHDOG_ONCE_NOTE)].rstrip() \\\n"
     "                + self.WATCHDOG_ONCE_NOTE\n",
     "            body = body[:8000]  # MUTANT\n",
     "the owner is not told why its dog vanished"),

    ("the note is appended and then truncated away",
     LEDGER,
     "            body = body[:8000 - len(self.WATCHDOG_ONCE_NOTE)].rstrip() \\\n"
     "                + self.WATCHDOG_ONCE_NOTE\n",
     "            body = (body + self.WATCHDOG_ONCE_NOTE)  # MUTANT\n",
     "a long event pushes the explanation off the end"),

    ("an ALERT also spends the dog",
     LEDGER,
     '        self._log("watchdog_alert", owner, {"id": wid, '
     '"why": body[:80]}, [])\n',
     '        self._log("watchdog_alert", owner, {"id": wid, '
     '"why": body[:80]}, [])\n'
     '        if w.get("once"):  # MUTANT\n'
     '            try:\n'
     '                self.d.setdefault("watchdogs", []).remove(w)\n'
     '            except ValueError:\n'
     '                pass\n',
     "a went-quiet report throws the watch away"),

    ("no canvas tombstone is written",
     LEDGER,
     '            tombs.append({"id": wid, "owner": owner, '
     '"name": str(w["name"]),\n',
     '            tombs.append({"id": "MUTANT", "owner": owner, '
     '"name": str(w["name"]),\n',
     "the spark has no origin and the fire is invisible"),

    ("tombstones never expire",
     LEDGER,
     "        return age < 0 or age > self.WATCHDOG_TOMB_TTL_S\n",
     "        return False  # MUTANT\n",
     "ghost dogs accumulate on the canvas forever"),

    ("_wd_fire reads `notice` AFTER the fire (the original bug)",
     SUP,
     "                w0 = org._watchdog(wid)\n"
     "                notice = bool(w0.get(\"notice\"))\n"
     "                one_shot = bool(w0.get(\"once\"))\n"
     "                kind = str(w0.get(\"kind\") or \"\")\n"
     "            except LedgerError:\n"
     "                notice = one_shot = False\n"
     "            owner = org.watchdog_fire(wid, lines[0] if lines else "
     "\"event\",\n"
     "                                      body)\n",
     "                one_shot = bool(org._watchdog(wid).get(\"once\"))\n"
     "                kind = str(org._watchdog(wid).get(\"kind\") or \"\")\n"
     "            except LedgerError:\n"
     "                one_shot = False\n"
     "            owner = org.watchdog_fire(wid, lines[0] if lines else "
     "\"event\",\n"
     "                                      body)\n"
     "            try:  # MUTANT: the pre-D-200 ordering\n"
     "                notice = bool(org._watchdog(wid).get(\"notice\"))\n"
     "            except LedgerError:\n"
     "                notice = False\n",
     "a one-shot NOTICE dog silently wakes its owner"),

    ("a spent one-shot stream's listener is not reaped",
     SUP,
     '    if one_shot and owner and kind == "stream":\n',
     '    if False and one_shot and owner and kind == "stream":  # MUTANT\n',
     "an orphaned listening process leaks on every use"),
]


def run_suite():
    p = subprocess.run([sys.executable, SUITE], cwd=BACKEND,
                       capture_output=True, text=True, timeout=900)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main():
    print("baseline (unmutated) …")
    rc, out = run_suite()
    if rc != 0:
        print(out[-4000:])
        print("☠ BASELINE IS RED — fix the suite before mutating anything.")
        return 1
    m = re.search(r"(\d+) passed, (\d+) failed", out)
    print(f"  baseline: {m.group(0) if m else '?'}\n")

    survivors = []
    for label, path, find, repl, why in MUTANTS:
        src = open(path, encoding="utf-8").read()
        if find not in src:
            print(f"  ✗ {label}\n      MUTATION DID NOT APPLY — the code it "
                  f"targets has moved. This mutant tested NOTHING.")
            survivors.append((label, "did not apply"))
            continue
        open(path, "w", encoding="utf-8", newline="").write(
            src.replace(find, repl, 1))
        try:
            rc, out = run_suite()
        finally:
            open(path, "w", encoding="utf-8", newline="").write(src)
        killed = rc != 0
        mm = re.search(r"(\d+) passed, (\d+) failed", out)
        print(f"  {'✓ killed ' if killed else '✗ SURVIVED'} {label}")
        print(f"      ({why}) -> {mm.group(0) if mm else 'no summary'}")
        if not killed:
            survivors.append((label, why))

    print()
    if survivors:
        print(f"☠ {len(survivors)} MUTANT(S) SURVIVED — that behaviour is "
              f"NOT actually checked:")
        for label, why in survivors:
            print(f"   · {label}  ({why})")
        return 1
    print(f"all {len(MUTANTS)} mutants killed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
