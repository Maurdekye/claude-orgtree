# pyright: strict
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
import uuid
from typing import TYPE_CHECKING, Any

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

# --- the one-disk sandbox (user hard requirement 2026-07-31; pivot shipped
# 2026-08-01, D-063: no one in the container may exhaust host disk beyond a
# fixed limit, while container state stays fully editable and survives
# restarts) ------------------------------------------------------------------
# Every sandboxed org's entire mutable state — system dirs, home incl.
# transcripts, workspace, scratch — lives on ONE fixed-size ext4 image,
# loop-mounted by the docker-desktop distro and bind-mounted into the
# container. The FILESYSTEM is the hard cap (ENOSPC); soft tiers in
# supervisor._storage_check_disk pause turns near it; the container is never
# stopped for storage. The rootfs runs READ-ONLY (no unmeasured writable
# surface); /tmp and /run are sized RAM tmpfs, bounded by --memory.
# The pre-disk design (per-org named volumes + reactive daemon-side
# measurement → container stop + freeze) is RETIRED — the SYS_DIRS volume
# names below survive only as migrate_to_disk's source material and the
# per-org rollback the admin sweeps from the settings panel.
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
    from .ledger import SYSTEM, now
    slug = org.d["slug"]
    k = org.d.get("kiosk") or {}
    size_mb = (int(k.get("storage_limit_mb") or 0)
               or int((org.d.get("sandbox") or {}).get("limit_mb") or 0)
               or DISK_MB)
    # one-disk semantics: the ~1 GB system seed and transcripts live INSIDE
    # the cap now — a limit written for the old workspace-only accounting
    # (e.g. 256 MB) cannot even hold the seed. Floor it.
    floored_from = 0
    if size_mb < 4096:
        floored_from = size_mb
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
        # the legacy enforcement is RETIRED (user ruling 2026-08-01, D-063):
        # clear any pre-migration storage freeze the doc still carries —
        # nothing sets or clears that flag anymore, so it would stick forever
        o2.d.pop("storage_frozen", None)
        for n in o2.nodes.values():
            fz = n.get("frozen")
            if isinstance(fz, dict) and fz.pop("storage", None):
                fz.pop("storage_error", None)
                if not fz.get("resume_texts") and not fz.get("error") \
                        and not fz.get("until"):
                    n.pop("frozen", None)
        if old_ws:
            for dd in o2.d["dirs"]:
                if os.path.normpath(dd["path"]) == os.path.normpath(old_ws):
                    dd["path"] = new_ws
            for n in o2.nodes.values():
                for dd in n["scope"]["add_dirs"]:
                    if os.path.normpath(dd["path"]) == os.path.normpath(old_ws):
                        dd["path"] = new_ws
        o2.d["workspace"] = new_ws
        if floored_from:
            # review 2026-08-01: flooring RAISES a cap the operator
            # deliberately set — that change belongs in their inbox, not
            # only the backend log
            o2.d.setdefault("user_inbox", []).append({
                "id": uuid.uuid4().hex[:12], "from": SYSTEM, "kind": "notice",
                "at": now(),
                "body": f"Storage migration: this org's {floored_from} MB "
                        f"limit was raised to the 4096 MB one-disk minimum "
                        f"(system seed + transcripts now count inside the "
                        f"cap). Its agents may consume up to 4 GB; the disk "
                        f"can be grown online or shrunk (staged) from the "
                        f"storage browser."})
        store.save_org(o2)
    _disk_flag.pop(slug, None)
    print(f"[orgtree] org {slug!r} migrated to its disk "
          f"({size_mb} MB; legacy volumes kept for rollback)")


_vm_cap_cache: tuple[float, int | None] | None = None


def vm_disk_cap_mib() -> int | None:
    """Docker Desktop's VM disk cap in MiB, or None when unset — the ABSOLUTE
    host bound on every container, image, and (sparse) org disk, no matter
    what this code does. A host setting we can only read; best-effort, the
    settings file location/keys are Docker Desktop internals. Surfaced
    IN-PRODUCT by the recovery browser (review suggestion 2026-08-01: the
    admin staring at storage is the right audience, not the backend log)."""
    global _vm_cap_cache
    if _vm_cap_cache and time.time() - _vm_cap_cache[0] < 300:
        return _vm_cap_cache[1]
    cap: int | None = None
    if os.name == "nt":
        try:
            p = os.path.join(os.environ.get("APPDATA", ""), "Docker",
                             "settings-store.json")
            cfg: dict[str, Any] = (json.load(open(p, encoding="utf-8"))
                                   if os.path.exists(p) else {})
            raw = next((v for k, v in cfg.items()
                        if k.lower() == "disksizemib"), None)
            cap = int(raw) if raw else None
        except Exception:                                # noqa: BLE001
            cap = None
    _vm_cap_cache = (time.time(), cap)
    return cap


_vm_cap_warned = False


def _warn_vm_cap() -> None:
    """One startup log line (the browser carries the in-product version)."""
    global _vm_cap_warned
    if _vm_cap_warned or os.name != "nt":
        return
    _vm_cap_warned = True
    cap = vm_disk_cap_mib()
    if cap:
        print(f"[orgtree] Docker VM disk cap: {cap} MiB "
              f"(the absolute sandbox storage backstop)")
    else:
        print("[orgtree] ⚠ Docker Desktop's disk image size is UNSET "
              "(defaults to a ~1 TB sparse disk) — the recovery browser "
              "shows this to the admin; set it in Docker Desktop → "
              "Settings → Resources.")

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


def uses_subscription_auth(k: dict[str, Any] | None) -> bool:
    """True when the org's sandbox would run on COPIED host credentials
    (the 'subscription' escape hatch — docstring: private single-user
    installs only). Security review 2026-08-01: this mode and a PUBLIC
    kiosk URL are mutually exclusive STRUCTURALLY — the OAuth token lands
    on the org disk, root-in-container can copy it to any path, and the
    recovery browser serves the disk to visitors; no filename denylist can
    be a boundary. Both enable-orderings are refused."""
    key = ((k or {}).get("api_key")
           or os.environ.get("ORGTREE_SANDBOX_API_KEY") or "proxied")
    return str(key).strip().lower() == "subscription"


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


def auth_label(org: Org, k: Any = None) -> str:
    """The container's auth as an identity token: `proxied`, `subscription`,
    or `key:<8 hex>` — a digest, never the key itself (labels are readable by
    anyone who can run `docker inspect`). Compared on every `ensure_container`
    so a settings change, a key rotation or an unset `ORGTREE_SANDBOX_API_KEY`
    recreates the container instead of leaving it billing the old way."""
    import hashlib
    auth = container_auth(org, k)
    low = auth.lower()
    if "prox" in low or low == "subscription":
        return low
    return "key:" + hashlib.sha256(auth.encode()).hexdigest()[:8]


def container_auth(org: Org, k: Any = None) -> str:
    """What a sandboxed org's container authenticates WITH: a literal API key,
    `"subscription"` (host credentials copied in), or `"proxied"` (the bridge
    attaches the host token per request).

    Factored out of `ensure_container` (redteam 2026-08-18) because a second
    caller needs the same answer and a hand-mirrored copy would drift:
    `supervisor.bills_the_key` has to know whether a limit error came off the
    org's own key or the host subscription, and reading `org.d["api_key"]`
    alone missed BOTH the kiosk-level key and `ORGTREE_SANDBOX_API_KEY` — a
    per-minute API rate limit was then timed off the subscription's lanes."""
    k = (org.d.get("kiosk") or {}) if k is None else k
    return (("" if org.d.get("api_fallback") else str(org.d.get("api_key") or ""))
            or str(k.get("api_key") or "")
            or os.environ.get("ORGTREE_SANDBOX_API_KEY")
            or "proxied").strip()


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


def chown_agent(org: Org, nid: str, *rel: str) -> None:
    """Hand a backend-minted path inside a sandboxed org to the agent.

    The backend writes through the \\\\wsl.localhost UNC view, and everything
    it creates lands root-owned inside the container — while the CLI runs as
    `agent` (uid 1001). A root-owned `outbox/` or `uploads/` reads to the
    agent as "my scratch is broken" (live bug 2026-08-04, kiosk `vnuser`).
    Best-effort by design: with the container down the exec fails silently,
    and the start-time heal in ensure_container covers it instead."""
    if not is_sandboxed(org):
        return
    slug = org.d["slug"]
    target = "/".join((cpath_scratch(slug, nid), *rel))
    try:
        # ⚠ -u root: exec inherits the image's USER agent, and an unprivileged
        # chown fails "Operation not permitted" — silently, given the swallow
        # below (caught live 2026-08-05 healing vnuser by hand)
        _docker("exec", "-u", "root", container_name(slug),
                "chown", "-R", "agent:agent", target, timeout=30)
    except Exception:                                        # noqa: BLE001
        pass


def _heal_ownership(name: str) -> None:
    """Every path the backend minted while the container was DOWN is
    root-owned (see chown_agent) — hand the whole data tree back to the agent
    at container start. Also fixes Docker's own root-owned mount scaffolding
    (/home/agent/orgtree, …/scratch, …/workspaces), which the agent sees when
    it looks one level above its own folder."""
    _docker("exec", "-u", "root", name, "chown", "-R", "agent:agent",
            cpath_data(), timeout=120)


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
    ins0 = _docker("container", "inspect", "-f", "{{.State.Running}}", name)
    if ins0.returncode != 0 or ins0.stdout.strip() != "true":
        # a natural container-down moment — the RULED trigger for a pending
        # shrink (it needs THIS org's container down, never the backend).
        # Best-effort: a refusal keeps the request pending, logged; the UI's
        # divergence chip keeps showing intent vs reality.
        try:
            note = try_apply_pending_resize(org)
            if note:
                print(f"[orgtree] org {slug!r}: {note}")
        except Exception as e:                          # noqa: BLE001
            print(f"[orgtree] org {slug!r}: pending-shrink attempt failed: {e}")
    # ⚠ mount-verify before EVERY start: /mnt/wsl dies with the WSL VM and
    # Docker mints an EMPTY DIR for a missing bind source — an org must
    # hard-refuse to run rather than bind an empty workspace and diverge.
    dsk.mount(slug)                        # sentinel-verified; raises DiskError
    ver = supervisor.cli_version()
    want = f"{IMAGE}:{ver}-{IMG_REV}" if ver != "unknown" else IMAGE
    ins = _docker("container", "inspect", "-f",
                  "{{.State.Running}} {{.Config.Image}} "
                  '{{index .Config.Labels "orgtree.layout"}} '
                  '{{index .Config.Labels "orgtree.auth"}}', name)
    if ins.returncode == 0:
        parts = ins.stdout.split()
        running = parts[0] if parts else ""
        cur_img = parts[1] if len(parts) > 1 else ""
        layout = parts[2] if len(parts) > 2 else ""
        cur_auth = parts[3] if len(parts) > 3 else ""
        # №44: the CLI rides the version-tagged read-only /usr/local volume,
        # so an image move requires a recreate (which re-mounts that volume);
        # a pre-disk layout recreates too (its state already migrated).
        # AUTH likewise (redteam 2026-08-18): `docker run -e
        # ANTHROPIC_API_KEY=…` bakes the credential in, so a container created
        # under one auth kept billing that way after the org's settings moved
        # — and `supervisor.bills_the_key`, which reads the CONFIG, then timed
        # the container's own API limits against the host subscription's
        # lanes. Recreating on change is what makes the config truthful.
        if (cur_img and cur_img != want) or layout != LAYOUT \
                or cur_auth != auth_label(org, k):
            _docker("rm", "-f", name, timeout=60)
        else:
            if running != "true":
                _docker("start", name)
                _heal_ownership(name)
            return name
    # auth (user ruling): PROXIED SUBSCRIPTION is the default for every kiosk
    # — the container's CLI talks to the bridge's /anthropic passthrough and
    # the HOST attaches the OAuth token; no credential ever enters the
    # sandbox. ORGTREE_SANDBOX_API_KEY remains a hidden escape hatch (a real
    # API key, or 'subscription' to copy the host credentials in).
    # §9.5: the ORG-LEVEL key (settings, any org — promoted out of the kiosk
    # spec) outranks the kiosk field; proxy mode and key mode stay mutually
    # exclusive — a set key wins and the bridge proxy is not used.
    # api_fallback (2026-08-17): a fallback org must stay PROXIED — container
    # env is fixed at `docker run`, so the per-request auth flip lives in the
    # bridge's /anthropic passthrough instead; skipping the org key here is
    # what routes it there
    key = container_auth(org, k)
    use_proxy = "prox" in key.lower()
    use_sub = key.lower() == "subscription"
    if use_sub and k.get("enabled") and k.get("token"):
        # structural, not a filter: see uses_subscription_auth
        raise RuntimeError(
            "subscription-auth sandbox with a PUBLIC kiosk URL is refused — "
            "the copied host credentials live on the org disk visitors can "
            "browse. Disable the kiosk URL or switch to proxied auth.")
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
        "--label", f"orgtree.auth={auth_label(org, k)}",
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
    _heal_ownership(name)
    return name


def try_apply_pending_resize(org: Org) -> str | None:
    """Apply a doc-persisted pending shrink (user design: record the request,
    apply when the org's container is down anyway, show the divergence until
    then). CALLER guarantees the container is down. Returns None when there
    was nothing pending or it applied; a human-readable reason when the
    shrink was KEPT PENDING (refuse-not-guess: usage may have outgrown the
    target while it waited — never partially apply, say how many MB to free)."""
    from . import disk as dsk
    slug = org.d["slug"]
    d = dict(org.d.get("disk") or {})
    pend = int(d.get("pending_size_mb") or 0)
    if not pend:
        return None
    dsk.mount(slug)                      # need a fresh usage reading
    du = dsk.usage(slug, max_age=0.0)
    # ~90% fit: resize2fs needs working room, and landing at 100% would be
    # an instant hard-full
    if du and du[0] > pend * 1048576 * 0.9:
        need = int((du[0] - pend * 1048576 * 0.9) / 1048576) + 1
        return (f"pending shrink to {pend} MB not applied: usage is "
                f"{du[0] // 1048576} MB — free about {need} MB first")
    dsk.shrink_image(slug, pend)
    dsk.mount(slug)
    with store.DOC_LOCK:
        o2 = store.load_org(slug)
        d2 = dict(o2.d.get("disk") or {})
        d2["size_mb"] = pend
        d2.pop("pending_size_mb", None)
        o2.d["disk"] = d2
        store.save_org(o2)
    print(f"[orgtree] org {slug!r}: pending shrink applied — disk is now "
          f"{pend} MB")
    return None


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
