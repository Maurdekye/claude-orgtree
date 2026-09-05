import subprocess, os, sys, shutil
ROOT = os.getcwd()
L = os.path.join(ROOT, "backend/orgtree/ledger.py")
A = os.path.join(ROOT, "backend/orgtree/api.py")
MUT = [
 ("no assignment notification", L,
  '        if not notify or own == actor or own == USER:',
  '        if True or not notify or own == actor or own == USER:'),
 ("being allowed to update counts as manage (the escalation)", L,
  '        pre_manage = self._work_can_manage(actor, it)',
  '        pre_manage = self._work_can_manage(actor, it) or actor != USER'),
 ("reply falls back to the last updater", L,
  '        own = self._work_actor_node(it.get("owner"))\r\n        if not own:',
  '        own = (self._work_actor_node(it.get("owner"))\r\n               or self._work_actor_node(it.get("last_updater")))\r\n        if not own:'),
 ("explicit owner ignored on update", L,
  '        tgt = str(owner or "").strip()\r\n        if tgt and tgt != actor and not pre_manage:',
  '        tgt = ""\r\n        if tgt and tgt != actor and not pre_manage:'),
 ("self-review not re-checked at decision time", L,
  '        if actor != USER and actor == own:\r\n            # the self-review prohibition',
  '        if False and actor != USER and actor == own:\r\n            # the self-review prohibition'),
 ("reviewer not required on entering review", L,
  '            if entering and not self._work_actor_node(it.get("reviewer")):',
  '            if False and entering and not self._work_actor_node(it.get("reviewer")):'),
 ("seat not driven by the assignment", A,
  '            if not ares.get("deferred"):\r\n                seat_drive = True',
  '            if False:\r\n                seat_drive = True'),
 ("reviewer-only actor may post an update", L,
  '        if actor != USER and not pre_manage \\r\n                and actor not in (it.get("participants") or []):',
  '        if False and actor != USER and not pre_manage \\r\n                and actor not in (it.get("participants") or []):'),
]
env = dict(os.environ, ORGTREE_DATA=os.path.join(ROOT, "..", "testdata-mut"))
os.makedirs(env["ORGTREE_DATA"], exist_ok=True)
for label, path, old, new in MUT:
    src = open(path, encoding="utf-8", newline="").read()
    if src.count(old) != 1:
        print(f"SKIP  {label}: anchor count {src.count(old)}"); continue
    open(path, "w", encoding="utf-8", newline="").write(src.replace(old, new))
    try:
        r = subprocess.run([sys.executable, "backend/tests/test_work_items.py"],
                           capture_output=True, text=True, env=env, cwd=ROOT)
        tail = [l for l in r.stdout.splitlines() if "passed," in l]
        fails = [l.strip() for l in r.stdout.splitlines() if l.startswith("  x ")]
        print(("REJECTED " if r.returncode else "⚠ SURVIVED") + f"  {label}: "
              + (tail[-1] if tail else "no summary"))
        for f in fails[:3]:
            print("        by:", f[4:])
    finally:
        open(path, "w", encoding="utf-8", newline="").write(src)
