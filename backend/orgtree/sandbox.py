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


def sandbox_home(slug: str) -> str:
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
    ins = _docker("container", "inspect", "-f",
                  "{{.State.Running}} {{.Config.Image}} "
                  "{{.HostConfig.ReadonlyRootfs}}", name)
    if ins.returncode == 0:
        parts = ins.stdout.split()
        running = parts[0] if parts else ""
        cur_img = parts[1] if len(parts) > 1 else ""
        hardened = (parts[2] if len(parts) > 2 else "") == "true"
        # №44: the host CLI updated → the versioned tag moved → recreate the
        # container on the new image instead of running the frozen old CLI.
        # (With the /usr volume the recreate alone no longer moves the CLI —
        # _sync_cli below updates it IN the volume.) An unhardened container
        # (pre-disk-bound build) recreates too; its state migrates into the
        # fresh volumes' image seed, losing only old writable-layer edits.
        from . import supervisor
        ver = supervisor.cli_version()
        want = f"{IMAGE}:{ver}-{IMG_REV}" if ver != "unknown" else IMAGE
        if (cur_img and cur_img != want) or not hardened:
            _docker("rm", "-f", name, timeout=60)
        else:
            if running != "true":
                _docker("start", name)
            _sync_cli(name)
            return name
    if not docker_ok():
        raise RuntimeError("Docker is not running — start Docker Desktop "
                           "(kiosk sandboxes run their turns in containers)")
    _warn_vm_cap()
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
    home = sandbox_home(slug)
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
    ws = org.d.get("workspace")
    os.makedirs(ws, exist_ok=True)   # type: ignore[arg-type]  # schema says workspace can be None — would TypeError here; latent bug reported, not fixed (typing wave = zero behavior change)
    scratch = store.scratch_root(slug)
    os.makedirs(scratch, exist_ok=True)
    # the only door out: backend URL + this org's secret, read by steer.py
    # and mcptool.py inside the container
    with open(os.path.join(home, "orgtree", ".bridge"), "w",
              encoding="utf-8") as f:
        json.dump({"url": bridge_url(), "secret": sandbox_secret(org)}, f)
    r = _docker(
        "run", "-d", "--name", name,
        "--memory", MEM, "--cpus", CPUS,
        # the disk bound: read-only rootfs + system-dir volumes + RAM tmpfs —
        # every persistent write lands somewhere measured (see SYS_DIRS note)
        "--read-only",
        "--tmpfs", f"/tmp:rw,size={TMP_SIZE},mode=1777",
        "--tmpfs", f"/run:rw,size={RUN_SIZE}",
        *[a for d in SYS_DIRS for a in ("-v", f"{sys_volume(slug, d)}:/{d}")],
        "--add-host", "host.docker.internal:host-gateway",
        *(["-e", "ANTHROPIC_BASE_URL="
               f"{bridge_url()}/anthropic/{sandbox_secret(org)}",
           "-e", "ANTHROPIC_API_KEY=orgtree-proxied"] if use_proxy
          else [] if use_sub
          else ["-e", "ANTHROPIC_API_KEY=" + key]),
        "-v", f"{home}:/home/agent",
        "-v", f"{ws}:{cpath_workspace(slug)}",
        "-v", f"{scratch}:{cpath_data()}/scratch/{slug}",
        "-v", f"{BACKEND_DIR}:/opt/orgtree-backend:ro",
        image_tag, "sleep", "infinity", timeout=300)
    if r.returncode != 0:
        raise RuntimeError("sandbox container failed to start: "
                           + (r.stderr or r.stdout)[-500:])
    _sync_cli(name)
    # the volumes' IMAGE SEED (~1 GB) is not the org's doing — record it as
    # the baseline so storage accounting charges only GROWTH (else a small
    # kiosk storage limit would breach at first boot). Measured after the
    # CLI sync so a version update isn't billed either.
    base = sandbox_volumes_bytes(slug, max_age=0.0)
    if base:
        with store.DOC_LOCK:
            o2 = store.load_org(slug)
            o2.d["sandbox_vols_base"] = base
            store.save_org(o2)
    return name


_cli_synced: set[str] = set()   # "name:ver" pairs verified this process


def _sync_cli(name: str) -> None:
    """№44 under volume shadowing: the /usr volume outlives image rebuilds, so
    a host CLI update no longer reaches the container via recreation alone.
    Verify the in-container CLI against the host and npm-update it IN the
    volume on mismatch (live-verified) — installs and system state survive,
    and the version invariant stays enforced rather than structural."""
    from . import supervisor
    ver = supervisor.cli_version()
    if ver == "unknown" or f"{name}:{ver}" in _cli_synced:
        return
    r = _docker("exec", name, "claude", "--version", timeout=60)
    have = (r.stdout or "").strip().split(" ")[0] if r.returncode == 0 else ""
    if have != ver:
        _docker("exec", "-u", "root", name, "npm", "install", "-g",
                f"@anthropic-ai/claude-code@{ver}", timeout=600)
        r2 = _docker("exec", name, "claude", "--version", timeout=60)
        have2 = (r2.stdout or "").strip().split(" ")[0] if r2.returncode == 0 else ""
        if have2 != ver:
            raise RuntimeError(
                f"sandbox CLI version sync failed for {name}: host {ver}, "
                f"container {have2 or 'unknown'} — check the container's "
                f"network/npm access (or remove its orgtree-sys-* volumes to "
                f"reseed from the image)")
    _cli_synced.add(f"{name}:{ver}")


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
