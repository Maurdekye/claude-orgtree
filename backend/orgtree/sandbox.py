"""Docker sandboxes for kiosk orgs (user spec).

Every kiosk org created with `sandbox: true` runs its agents' turns inside ONE
dedicated container (image: sandbox/Dockerfile) — genuine terminal use with no
view of the host: no host filesystem, no host processes, per-container CPU and
memory caps. Non-kiosk orgs are untouched and run natively.

Container layout (bind mounts):
    /home/agent                          <data>/sandboxes/<slug>/home   (persists
                                         ~/.claude — session transcripts — so
                                         resume-on-demand and read_chat work)
    /home/agent/orgtree/workspaces/<slug>   the org workspace (the ONE window)
    /home/agent/orgtree/scratch/<slug>      node scratch dirs (turn cwds)
    /opt/orgtree-backend                 this backend, read-only (mcptool.py +
                                         steer.py run in-container via python3)

The in-container data layout mirrors the host's ~/orgtree shape on purpose:
steer.py's cwd-derived identity and its ~/orgtree fallback work unchanged.
`.bridge` in the container's data root carries the backend URL
(host.docker.internal:<bridge port>) + the org's sandbox secret — the only
door out, gated by api.BridgeGateway to /api/agent and the steer fetch.

Auth: the default is the PROXIED SUBSCRIPTION — the container's CLI points at
the bridge's /anthropic/<secret> proxy (host-side OAuth, no credential file
ever enters the sandbox); a kiosk `api_key` (creation form / dashboard) or
ORGTREE_SANDBOX_API_KEY overrides it with a plain env key. The literal value
'subscription' copies the host credentials into the sandbox home — private
single-user installs only.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from typing import TYPE_CHECKING

from . import store

if TYPE_CHECKING:
    from .ledger import Org

_DATA: str = os.path.expanduser(os.environ.get("ORGTREE_DATA", "~/orgtree"))
REPO_ROOT: str = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
BACKEND_DIR: str = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))

IMAGE: str = os.environ.get("ORGTREE_SANDBOX_IMAGE", "orgtree-sandbox")
# bump when sandbox/Dockerfile changes: the tag carries the revision, so
# existing images rebuild and running containers recreate on their next turn
# (r2: passwordless sudo — agents hold root inside the container)
IMG_REV: str = "r2"
BRIDGE_PORT: int = int(os.environ.get("ORGTREE_BRIDGE_PORT", "7362") or 0)
MEM: str = os.environ.get("ORGTREE_SANDBOX_MEM", "4g")
CPUS: str = os.environ.get("ORGTREE_SANDBOX_CPUS", "2")

# --- bounded persistent sandbox (user hard requirement 2026-07-31: no one in
# the container may exhaust host disk beyond a fixed limit, while container
# state stays fully editable and survives restarts) -------------------------
# The rootfs runs READ-ONLY, so no unmeasured writable surface exists. The
# system dirs agents legitimately edit are per-org named volumes, auto-seeded
# from the image on first mount (live-verified: apt install, sudo, and the
# docker-managed /etc/hosts binds all work; installs now survive container
# RECREATION, which the old writable layer never did). /tmp and /run are
# sized RAM tmpfs, already bounded by --memory. Named volumes have no quota
# on Docker Desktop (overlay2/ext4 — no --storage-opt), so the per-org limit
# is enforced reactively by storage_check (measure daemon-side → stop the
# container + freeze); the ABSOLUTE host bound is Docker Desktop's VM disk
# cap (Settings → Resources → disk image size), see README.
SYS_DIRS: tuple[str, ...] = ("usr", "var", "etc", "opt", "root", "srv")
TMP_SIZE: str = os.environ.get("ORGTREE_SANDBOX_TMP", "1g")
RUN_SIZE: str = os.environ.get("ORGTREE_SANDBOX_RUN", "64m")
# default per-org disk limit (MB) for sandboxed orgs whose kiosk/org config
# doesn't set one — 0 disables the default (NOT recommended: unbounded)
DISK_MB: int = int(os.environ.get("ORGTREE_SANDBOX_DISK_MB", "20480") or 0)


def sys_volume(slug: str, d: str) -> str:
    return f"orgtree-sys-{slug}-{d}"


# container layout generation — a label on the container; mismatch = recreate
LAYOUT: str = "disk-v1"


def usrlocal_volume(ver: str) -> str:
    """The version-tagged READ-ONLY /usr/local (user verdict: the CLI stays
    image-pinned even though /usr rides the org disk). Docker seeds it from
    the image on first mount; the version in the name is the №44 invariant —
    a host CLI update moves the name, and the fresh volume seeds from the
    freshly built image."""
    return f"orgtree-usrlocal-{ver}-{IMG_REV}"


def migrate_to_disk(org: Org) -> None:
    """One-time move of a sandboxed org onto its virtual disk (user-approved
    auto-migration): create+mount the disk, copy the org's whole state into
    it — system dirs from its legacy volumes (preserving installs) or the
    image, home/workspace/scratch from the host dirs — verify per-tree file
    counts, then flip the org doc's paths. Old volumes and host dirs are
    KEPT (rollback: clear org.d['disk'] and restart). The calling turn waits;
    a multi-GB org takes minutes, once."""
    from . import disk as dsk
    from .ledger import now
    slug = org.d["slug"]
    k = org.d.get("kiosk") or {}
    size_mb = (int(k.get("storage_limit_mb") or 0)
               or int((org.d.get("sandbox") or {}).get("limit_mb") or 0)
               or DISK_MB)
    # one-disk semantics: the ~1 GB system seed and transcripts live INSIDE
    # the cap now — a limit written for the old workspace-only accounting
    # (e.g. 256 MB) cannot even hold the seed. Floor it.
    if size_mb < 4096:
        print(f"[orgtree] org {slug!r}: configured storage limit {size_mb} MB "
              f"is below the 4096 MB one-disk minimum (the system seed and "
              f"transcripts count now) — using 4096 MB")
        size_mb = 4096
    print(f"[orgtree] migrating org {slug!r} onto its {size_mb} MB disk …")
    dsk.create(slug, size_mb)
    _docker("stop", "-t", "20", container_name(slug), timeout=60)
    image_tag = ensure_image()
    old_home = os.path.join(sandbox_root(slug), "home")
    old_ws = org.d.get("workspace")
    old_scratch = store.scratch_root(slug)
    vols = {d: sys_volume(slug, d)
            for d in ("usr", "var", "etc", "opt", "root", "srv")
            if _docker("volume", "inspect", sys_volume(slug, d)).returncode == 0}
    args: list[str] = ["run", "--rm", "-u", "root",
                       "-v", f"{dsk.mount_path(slug)}:/dst"]
    for d, v in vols.items():
        args += ["-v", f"{v}:/old/{d}:ro"]
    for sub, host in (("home", old_home), ("workspace", old_ws),
                      ("scratch", old_scratch)):
        if host and os.path.isdir(host):
            args += ["-v", f"{host}:/oldhost/{sub}:ro"]
    # system dirs seed from the legacy volume when present, else the image's
    # own rootfs (this helper RUNS that image); host trees copy when present.
    # cp -a preserves modes/links; each tree is count-verified src vs dst.
    script = (
        "set -e; AUID=$(id -u agent); AGID=$(id -g agent); "
        "for d in usr var etc opt root srv; do "
        "  mkdir -p /dst/$d; "
        "  if [ -d /old/$d ]; then S=/old/$d; else S=/$d; fi; "
        "  cp -a $S/. /dst/$d/; "
        "  a=$(find $S -type f | wc -l); b=$(find /dst/$d -type f | wc -l); "
        "  [ \"$a\" = \"$b\" ] || { echo MISMATCH $d $a $b; exit 9; }; "
        "done; "
        "for s in home workspace scratch; do "
        "  mkdir -p /dst/$s; "
        "  if [ -d /oldhost/$s ]; then "
        "    cp -a /oldhost/$s/. /dst/$s/; "
        "    a=$(find /oldhost/$s -type f | wc -l); "
        "    b=$(find /dst/$s -type f | wc -l); "
        "    [ \"$a\" = \"$b\" ] || { echo MISMATCH $s $a $b; exit 9; }; "
        "  fi; "
        "  chown -R $AUID:$AGID /dst/$s; "
        "done; echo MIGRATED")
    r = _docker(*args, image_tag, "sh", "-c", script, timeout=3600)
    if r.returncode != 0 or "MIGRATED" not in (r.stdout or ""):
        raise RuntimeError(f"org {slug!r} disk migration failed — nothing "
                           f"was flipped; old state is untouched: "
                           + (r.stderr or r.stdout)[-500:])
    new_ws = dsk.windows_sub(slug, "workspace")
    with store.DOC_LOCK:
        o2 = store.load_org(slug)
        o2.d["disk"] = {"size_mb": size_mb, "migrated_at": now()}
        if old_ws:
            for dd in o2.d["dirs"]:
                if os.path.normpath(dd["path"]) == os.path.normpath(old_ws):
                    dd["path"] = new_ws
            for n in o2.nodes.values():
                for dd in n["scope"]["add_dirs"]:
                    if os.path.normpath(dd["path"]) == os.path.normpath(old_ws):
                        dd["path"] = new_ws
        o2.d["workspace"] = new_ws
        store.save_org(o2)
    _disk_flag.pop(slug, None)
    print(f"[orgtree] org {slug!r} migrated to its disk "
          f"({size_mb} MB; legacy volumes kept for rollback)")


_vm_cap_checked = False


def _warn_vm_cap() -> None:
    """The ABSOLUTE host bound is Docker Desktop's VM disk cap — the one thing
    that bounds every container, image, and volume no matter what this code
    does. It is a host setting we can only read: warn once per process when it
    is unset (the default is a ~1 TB sparse VHDX). Best-effort — the settings
    file location/keys are Docker Desktop internals."""
    global _vm_cap_checked
    if _vm_cap_checked or os.name != "nt":
        return
    _vm_cap_checked = True
    try:
        p = os.path.join(os.environ.get("APPDATA", ""), "Docker",
                         "settings-store.json")
        cfg = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
        cap = next((v for k, v in cfg.items()
                    if k.lower() == "disksizemib"), None)
        if not cap:
            print("[orgtree] ⚠ Docker Desktop's disk image size is UNSET "
                  "(defaults to a ~1 TB sparse disk). That cap is the only "
                  "ABSOLUTE bound on sandbox disk use — set it in Docker "
                  "Desktop → Settings → Resources (see README, sandbox "
                  "storage). Per-org limits are enforced reactively on top.")
        else:
            print(f"[orgtree] Docker VM disk cap: {int(cap)} MiB "
                  f"(the absolute sandbox storage backstop)")
    except Exception:                                    # noqa: BLE001
        pass

_build_lock: threading.Lock = threading.Lock()


def _cfg(org: Org) -> dict[str, str] | None:
    """Sandbox config for ANY org (user ruling: not just kiosks): kiosks
    carry it inside their kiosk dict; normal orgs in a top-level `sandbox`."""
    k = org.d.get("kiosk") or {}
    if k.get("sandbox"):
        return {"secret": k.get("sandbox_secret", "")}
    s = org.d.get("sandbox") or {}
    if s.get("enabled"):
        return {"secret": s.get("secret", "")}
    return None


def is_sandboxed(org: Org) -> bool:
    return _cfg(org) is not None


_docker_ok: bool | None = None


def docker_available() -> bool:
    """Is a docker CLI on PATH? (cached — the UI disables the sandbox
    checkbox entirely when it isn't; user ruling)"""
    global _docker_ok
    if _docker_ok is None:
        _docker_ok = shutil.which("docker") is not None
    return _docker_ok


def sandbox_secret(org: Org) -> str:
    return (_cfg(org) or {}).get("secret", "")


def container_name(slug: str) -> str:
    return "orgtree-" + slug


def sandbox_root(slug: str) -> str:
    return os.path.join(_DATA, "sandboxes", slug)


# virtual-disk pivot (user verdict 2026-07-31): a migrated org's state lives
# ON its disk; host-side readers must follow it there. The flag is on the org
# doc — cached briefly so hot paths (usage walks, transcript reads) don't
# re-load the doc per call.
_disk_flag: dict[str, tuple[float, bool]] = {}


def on_disk(slug: str) -> bool:
    hit = _disk_flag.get(slug)
    if hit and time.time() - hit[0] < 10:
        return hit[1]
    try:
        val = bool(store.load_org(slug).d.get("disk"))
    except Exception:                                    # noqa: BLE001
        val = False
    _disk_flag[slug] = (time.time(), val)
    return val


def sandbox_home(slug: str) -> str:
    if on_disk(slug):
        from . import disk
        return disk.windows_sub(slug, "home")
    return os.path.join(sandbox_root(slug), "home")


# in-container paths (the mirror of the host layout)
def cpath_data() -> str:
    return "/home/agent/orgtree"


def cpath_workspace(slug: str) -> str:
    return f"{cpath_data()}/workspaces/{slug}"


def cpath_scratch(slug: str, nid: str) -> str:
    return f"{cpath_data()}/scratch/{slug}/{nid.split('@')[0]}"


def bridge_url() -> str:
    return f"http://host.docker.internal:{BRIDGE_PORT}"


def _docker(*args: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True,
                          timeout=timeout)


def docker_ok() -> bool:
    try:
        return _docker("version", "--format", "{{.Server.Version}}").returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def ensure_image() -> str:
    """№44 (user-approved): the image is TAGGED with the host CLI's version
    and pins the same version inside — when the host CLI updates, the next
    sandboxed turn rebuilds instead of running a CLI frozen at first-build.
    Returns the tag to run."""
    from . import supervisor        # lazy — supervisor imports this module
    ver = supervisor.cli_version()
    tag = f"{IMAGE}:{ver}-{IMG_REV}" if ver != "unknown" else IMAGE
    if _docker("image", "inspect", tag).returncode == 0:
        return tag
    with _build_lock:
        if _docker("image", "inspect", tag).returncode == 0:
            return tag
        args = ["build", "-t", tag]
        if ver != "unknown":
            args += ["--build-arg", f"CLAUDE_VERSION={ver}"]
        r = _docker(*args, os.path.join(REPO_ROOT, "sandbox"), timeout=1200)
        if r.returncode != 0:
            raise RuntimeError("sandbox image build failed: "
                               + (r.stderr or r.stdout)[-500:])
    return tag


def ensure_container(org: Org) -> str:
    """The org's container, created on first need and restarted if stopped.
    Raises RuntimeError with an actionable message when it cannot run."""
    slug = org.d["slug"]
    k = org.d.get("kiosk") or {}
    name = container_name(slug)
    if not docker_ok():
        raise RuntimeError("Docker is not running — start Docker Desktop "
                           "(kiosk sandboxes run their turns in containers)")
    _warn_vm_cap()
    from . import disk as dsk, supervisor
    # virtual-disk pivot (user verdict): every sandboxed org rides its own
    # capped ext4 disk. Legacy (volume-layout) orgs migrate on first need —
    # user-approved auto-migration; old volumes are KEPT for rollback.
    if not org.d.get("disk"):
        migrate_to_disk(org)
        org = store.load_org(slug)        # workspace/dirs were rewritten
    # ⚠ mount-verify before EVERY start: /mnt/wsl dies with the WSL VM and
    # Docker mints an EMPTY DIR for a missing bind source — an org must
    # hard-refuse to run rather than bind an empty workspace and diverge.
    dsk.mount(slug)                        # sentinel-verified; raises DiskError
    ver = supervisor.cli_version()
    want = f"{IMAGE}:{ver}-{IMG_REV}" if ver != "unknown" else IMAGE
    ins = _docker("container", "inspect", "-f",
                  "{{.State.Running}} {{.Config.Image}} "
                  '{{index .Config.Labels "orgtree.layout"}}', name)
    if ins.returncode == 0:
        parts = ins.stdout.split()
        running = parts[0] if parts else ""
        cur_img = parts[1] if len(parts) > 1 else ""
        layout = parts[2] if len(parts) > 2 else ""
        # №44: the CLI rides the version-tagged read-only /usr/local volume,
        # so an image move requires a recreate (which re-mounts that volume);
        # a pre-disk layout recreates too (its state already migrated).
        if (cur_img and cur_img != want) or layout != LAYOUT:
            _docker("rm", "-f", name, timeout=60)
        else:
            if running != "true":
                _docker("start", name)
            return name
    # auth (user ruling): PROXIED SUBSCRIPTION is the default for every kiosk
    # — the container's CLI talks to the bridge's /anthropic passthrough and
    # the HOST attaches the OAuth token; no credential ever enters the
    # sandbox. ORGTREE_SANDBOX_API_KEY remains a hidden escape hatch (a real
    # API key, or 'subscription' to copy the host credentials in).
    key = (k.get("api_key") or os.environ.get("ORGTREE_SANDBOX_API_KEY")
           or "proxied").strip()
    use_proxy = "prox" in key.lower()
    use_sub = key.lower() == "subscription"
    image_tag = ensure_image()
    home = sandbox_home(slug)              # on-disk, via the UNC view
    os.makedirs(os.path.join(home, "orgtree"), exist_ok=True)
    if use_sub:
        os.makedirs(os.path.join(home, ".claude"), exist_ok=True)
        src = os.path.expanduser("~/.claude/.credentials.json")
        if not os.path.isfile(src):
            raise RuntimeError("subscription mode: no Claude credentials found "
                               "at ~/.claude/.credentials.json")
        import shutil as _sh
        _sh.copy2(src, os.path.join(home, ".claude", ".credentials.json"))
        cfg = os.path.join(home, ".claude.json")
        if not os.path.exists(cfg):
            with open(cfg, "w", encoding="utf-8") as f:
                json.dump({"hasCompletedOnboarding": True}, f)
    # the only door out: backend URL + this org's secret, read by steer.py
    # and mcptool.py inside the container
    with open(os.path.join(home, "orgtree", ".bridge"), "w",
              encoding="utf-8") as f:
        json.dump({"url": bridge_url(), "secret": sandbox_secret(org)}, f)
    mp = dsk.mount_path(slug)
    r = _docker(
        "run", "-d", "--name", name,
        "--label", f"orgtree.layout={LAYOUT}",
        "--memory", MEM, "--cpus", CPUS,
        # ONE capped disk (user verdict): rootfs read-only, every persistent
        # write — system dirs, home incl. transcripts, workspace, scratch —
        # lands on the org's ext4 image; ENOSPC is the enforcement. /tmp and
        # /run are RAM, bounded by --memory. /usr/local is the version-tagged
        # READ-ONLY volume: the CLI stays image-pinned under the /usr shadow.
        "--read-only",
        "--tmpfs", f"/tmp:rw,size={TMP_SIZE},mode=1777",
        "--tmpfs", f"/run:rw,size={RUN_SIZE}",
        *[a for d in SYS_DIRS for a in ("-v", f"{mp}/{d}:/{d}")],
        "-v", f"{usrlocal_volume(ver)}:/usr/local:ro",
        "--add-host", "host.docker.internal:host-gateway",
        *(["-e", "ANTHROPIC_BASE_URL="
               f"{bridge_url()}/anthropic/{sandbox_secret(org)}",
           "-e", "ANTHROPIC_API_KEY=orgtree-proxied"] if use_proxy
          else [] if use_sub
          else ["-e", "ANTHROPIC_API_KEY=" + key]),
        "-v", f"{mp}/home:/home/agent",
        "-v", f"{mp}/workspace:{cpath_workspace(slug)}",
        "-v", f"{mp}/scratch:{cpath_data()}/scratch/{slug}",
        "-v", f"{BACKEND_DIR}:/opt/orgtree-backend:ro",
        image_tag, "sleep", "infinity", timeout=300)
    if r.returncode != 0:
        raise RuntimeError("sandbox container failed to start: "
                           + (r.stderr or r.stdout)[-500:])
    return name


def stop_container(slug: str) -> None:
    """Storage-breach lever: halt the org's container so volume growth stops.
    20 s grace lets the CLI flush transcript appends (they live on the home
    bind, which stays writable — engine state is never starved by the cap)."""
    try:
        _docker("stop", "-t", "20", container_name(slug), timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        pass


_vol_usage_cache: dict[str, tuple[float, int]] = {}


def _parse_size(s: str) -> int:
    """Docker's human sizes ("5.34MB", "1.06GB", "0B") — SI decimal units."""
    s = (s or "").strip()
    units = {"B": 1, "KB": 10**3, "MB": 10**6, "GB": 10**9, "TB": 10**12}
    for u, mul in sorted(units.items(), key=lambda kv: -len(kv[0])):
        if s.upper().endswith(u):
            try:
                return int(float(s[:-len(u)]) * mul)
            except ValueError:
                return 0
    try:
        return int(float(s))
    except ValueError:
        return 0


def sandbox_volumes_bytes(slug: str, max_age: float = 60.0) -> int | None:
    """Bytes in the org's system volumes, measured DAEMON-SIDE (`docker system
    df -v`), so it works with the container stopped — the auto-unblock path
    must keep measuring after a breach stop. Returns None on TIMEOUT, which
    callers treat as a breach (fail closed: an adversary can make the size
    walk pathologically slow with millions of tiny files); returns 0 when
    docker is unavailable (nothing can be growing either)."""
    hit = _vol_usage_cache.get(slug)
    if hit and time.time() - hit[0] < max_age:
        return hit[1]
    try:
        r = _docker("system", "df", "-v", "--format", "{{json .Volumes}}",
                    timeout=120)
    except subprocess.TimeoutExpired:
        return None
    except OSError:
        return 0
    if r.returncode != 0:
        return 0
    try:
        vols = json.loads(r.stdout or "[]")
    except json.JSONDecodeError:
        return 0
    prefix = f"orgtree-sys-{slug}-"
    total = sum(_parse_size(v.get("Size", "0B")) for v in vols
                if str(v.get("Name", "")).startswith(prefix))
    _vol_usage_cache[slug] = (time.time(), total)
    return total


def sandbox_volumes_cached(slug: str) -> int | None:
    """Cache-only volume reading for REQUEST paths (the df walk can take
    seconds — same rule as workspace_usage_cached: never walk inline).
    Enforcement keeps the cache warm; None until the first measurement."""
    hit = _vol_usage_cache.get(slug)
    return hit[1] if hit else None


def exec_argv(name: str, cwd: str) -> list[str]:
    """Prefix that runs a command inside the org's container."""
    return ["docker", "exec", "-i", "-w", cwd, name]


def kill_claude(name: str, match: str = "claude") -> None:
    """Timeout hammer: killing the `docker exec` client on the host leaves the
    in-container process alive — reap it explicitly. `match` narrows the kill
    to one turn's process (its session id appears in the argv); the container
    is shared org-wide, so a blanket "claude" match would SIGKILL every other
    agent's turn too (gap audit №40)."""
    try:
        _docker("exec", name, "pkill", "-9", "-f", match, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        pass


_dead: set[str] = set()   # slugs deleted mid-warm-up (create/delete race)


def remove(slug: str) -> None:
    """Org deleted: tear the container AND its system volumes down (orphaned
    volumes are unmeasured GBs — exactly what the disk bound exists to stop;
    the org's FILES survive via the trash-rename of its doc + host dirs, but
    container system state follows the container). The tombstone closes the
    warm() race: deleting an org while its background prebuild was still
    creating the container used to leak it (rm -f fired before the container
    existed; observed in the wild)."""
    _dead.add(slug)
    try:
        _docker("rm", "-f", container_name(slug), timeout=60)
        _docker("volume", "rm", "-f", *[sys_volume(slug, d) for d in SYS_DIRS],
                timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        pass
    from . import disk as dsk
    try:
        dsk.destroy(slug)
    except Exception:                                    # noqa: BLE001
        pass
    _disk_flag.pop(slug, None)


def warm(org: Org) -> None:
    """Fire-and-forget prebuild at kiosk creation so the first turn is not
    minutes slow (image build + container create)."""
    slug = org.d["slug"]
    _dead.discard(slug)          # same-slug re-create un-tombs it

    def run() -> None:
        try:
            ensure_container(org)
        except Exception as e:              # noqa: BLE001 — surfaced per turn
            print(f"[orgtree] sandbox warm-up for {slug!r}: {e}")
        if slug in _dead:        # deleted while we were building — tear down
            try:
                _docker("rm", "-f", container_name(slug), timeout=60)
            except (OSError, subprocess.TimeoutExpired):
                pass
    threading.Thread(target=run, daemon=True).start()
