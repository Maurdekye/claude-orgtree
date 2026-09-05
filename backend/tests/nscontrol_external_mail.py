"""NEGATIVE CONTROL for §12 of test_external_mail.py — the namespace boundary.

    python backend/tests/nscontrol_external_mail.py

§12 asserts that every route which writes an OUTSIDE party's name into an
AGENT's mailbox namespaces it first (`@mcp:` / `@org:` / `@net:`). The desk
transcript's identity rule rests on that: `MailFrom` (frontend
canvas/desk.tsx) refuses a chip and a route to any '@'-prefixed sender, so if
one inbound route ever landed a bare outside name in a mailbox, an outside
party could wear one of our agents' model chips and offer a link into our
tree.

An instrument reporting "they all namespace" has to prove it can find one that
does not. This strips the namespace at exactly ONE of the four production call
sites at a time, in a scratch copy of the file, and requires §12 to go red.

⚠ IT RUNS §12 ALONE, not the whole suite. §5 and §8 already assert the
ORG-INBOX LOG's peer for two of these routes, so a whole-suite run dies before
§12 is reached — which proves the mutant is caught, but not that §12 catches
it, and §12 is the check that speaks for the mailbox rather than the log.

⚠ IT EDITS backend/orgtree/*.py IN PLACE and restores the exact bytes in a
finally block. Run it in a worktree, never in a shared checkout.

The section stops at its first failing check and never prints that check's
label, so "killed" is: the run is red, the traceback names
s12_namespace_boundary, and this route's own label never printed as an `ok`.
"""
import io
import os
import pathlib
import subprocess
import sys
import textwrap

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent                                   # backend/
PKG = ROOT / "orgtree"
SUITE = HERE / "test_external_mail.py"

MUTANTS = [
    (PKG / "api.py", '    return f"@mcp:{peer}"', '    return peer',
     "the @mcp: route"),
    (PKG / "supervisor.py",
     '    deliver_org_inbox(dst_slug, f"@org:{src_slug}", body)',
     '    deliver_org_inbox(dst_slug, src_slug, body)',
     "the @org: route: another org"),
    (PKG / "api.py",
     'supervisor.deliver_org_inbox(dst, f"@org:{slug}", body.body,',
     'supervisor.deliver_org_inbox(dst, slug, body.body,',
     "the @org: route again, from the org-inbox COMPOSER"),
    (PKG / "net.py",
     'supervisor.deliver_org_inbox(slug, f"@net:{m.get(\'from\')}", body,',
     'supervisor.deliver_org_inbox(slug, str(m.get(\'from\')), body,',
     "the @net: route"),
]

# a driver that imports the suite (its module body builds its own throwaway
# ORGTREE_DATA before any orgtree import) and calls §12 and nothing else
DRIVER = textwrap.dedent("""
    import importlib.util, shutil, sys
    PATH = %r
    sys.argv = [PATH, '--hermetic']
    spec = importlib.util.spec_from_file_location('extmail_s12_only', PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    try:
        mod.s12_namespace_boundary()
        print('\\n(section 12 only) %%d checks pass' %% mod.PASS)
    finally:
        mod.supervisor.send_message = mod._real_send_message
        for d in mod.SCRATCH:
            shutil.rmtree(d, ignore_errors=True)
""") % str(SUITE)


def main() -> int:
    drv = HERE / "_s12_only_driver.py"
    drv.write_text(DRIVER, encoding="utf-8")
    env = dict(os.environ)
    # belt and braces: the suite sets its own throwaway root before importing
    # orgtree, and this makes the LIVE root unreachable even if that changed
    env["ORGTREE_DATA"] = str(HERE / "_ns_throwaway")
    bad = 0
    try:
        for path, frm, to, label in MUTANTS:
            raw = path.read_bytes()
            s = raw.decode("utf-8")
            if s.count(frm) != 1:
                print(f"STALE (found {s.count(frm)}x) — {label}: this control "
                      f"no longer names a real call site in {path.name}")
                bad += 1
                continue
            try:
                path.write_bytes(s.replace(frm, to).encode("utf-8"))
                r = subprocess.run([sys.executable, str(drv)],
                                   capture_output=True, text=True, env=env)
                out = (r.stdout or "") + (r.stderr or "")
                red = r.returncode != 0
                in_s12 = "s12_namespace_boundary" in out
                passed_anyway = label in out
                if red and in_s12 and not passed_anyway:
                    print(f"killed   — {label}")
                elif red and not in_s12:
                    print(f"WRONG CHECK — {label}: red outside §12")
                    bad += 1
                elif red:
                    print(f"WRONG CHECK — {label}: §12 red, but this route "
                          f"still passed")
                    bad += 1
                else:
                    print(f"SURVIVED — {label}: §12 stayed GREEN with the "
                          f"namespace stripped")
                    bad += 1
            finally:
                path.write_bytes(raw)          # exact bytes, endings and all
    finally:
        drv.unlink(missing_ok=True)
        import shutil
        shutil.rmtree(HERE / "_ns_throwaway", ignore_errors=True)
    print(f"\n{len(MUTANTS) - bad}/{len(MUTANTS)} killed")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
