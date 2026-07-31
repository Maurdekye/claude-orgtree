"""Live drill for orgtree.disk (stage 1 of the virtual-disk pivot).

Pins the properties the design rests on, against real Docker/WSL:
sentinel semantics, container-bind writes, the ENOSPC hard cap, usage(),
Windows-side deletion AT 100% FULL, online grow, distro-side enumeration,
and remount-after-teardown. Run:  python tools/disk_drill.py
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from orgtree import disk  # noqa: E402

SLUG = "disk-drill"
IMAGE = "orgtree-sandbox:2.1.220-r2"
checks: list[tuple[str, bool]] = []


def check(name: str, ok: bool) -> None:
    checks.append((name, ok))
    print(("✓" if ok else "✗"), name)


try:
    disk.create(SLUG, 64)
    check("create+mount", disk.is_mounted(SLUG))

    r = subprocess.run(
        ["docker", "run", "--rm", "-v", f"{disk.mount_path(SLUG)}:/disk",
         IMAGE, "sh", "-c",
         "echo data > /disk/a.txt && "
         "dd if=/dev/zero of=/disk/fill bs=1M count=200 2>&1; "
         "df /disk | tail -1"],
        capture_output=True, text=True, timeout=120,
        env={**os.environ, "MSYS_NO_PATHCONV": "1"})
    check("container write", "a.txt" not in (r.stderr or ""))
    check("ENOSPC at cap", "No space left" in r.stdout + r.stderr)

    u = disk.usage(SLUG, max_age=0.0)
    check("usage() ~full", u is not None and u[0] / u[1] > 0.85)

    files = disk.enumerate_by_size(SLUG, limit=10)
    check("enumerate by size desc",
          bool(files) and files[0]["path"] == "fill")

    wp = disk.windows_path(SLUG)
    fill = os.path.join(wp, "fill")
    os.unlink(fill)                       # THE recovery property: at 100% full
    check("windows delete at 100% full", not os.path.exists(fill))
    u2 = disk.usage(SLUG, max_age=0.0)
    check("usage dropped", u2 is not None and u2[0] < (u[0] if u else 0))

    disk.grow(SLUG, 128)
    u3 = disk.usage(SLUG, max_age=0.0)
    check("online grow 64→128MB",
          u3 is not None and u3[1] > 100 * 1048576)

    disk.unmount(SLUG)
    check("sentinel gone after unmount", not disk.is_mounted(SLUG))
    disk.mount(SLUG)
    check("remount restores content",
          disk.is_mounted(SLUG)
          and os.path.exists(os.path.join(wp, "a.txt")))
finally:
    disk.destroy(SLUG)
    check("destroy leaves no volume", subprocess.run(
        ["docker", "volume", "inspect", disk.disk_volume(SLUG)],
        capture_output=True).returncode != 0)

fails = [n for n, ok in checks if not ok]
print("\nRESULT:", "PASS" if not fails else f"FAIL: {fails}")
sys.exit(1 if fails else 0)
