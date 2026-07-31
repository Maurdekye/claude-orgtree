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

from . import store

_DATA = os.path.expanduser(os.environ.get("ORGTREE_DATA", "~/orgtree"))
REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
BACKEND_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))

IMAGE = os.environ.get("ORGTREE_SANDBOX_IMAGE", "orgtree-sandbox")
BRIDGE_PORT = int(os.environ.get("ORGTREE_BRIDGE_PORT", "7362") or 0)
MEM = os.environ.get("ORGTREE_SANDBOX_MEM", "4g")
CPUS = os.environ.get("ORGTREE_SANDBOX_CPUS", "2")

_build_lock = threading.Lock()


def _cfg(org) -> dict | None:
    """Sandbox config for ANY org (user ruling: not just kiosks): kiosks
    carry it inside their kiosk dict; normal orgs in a top-level `sandbox`."""
    k = org.d.get("kiosk") or {}
    if k.get("sandbox"):
        return {"secret": k.get("sandbox_secret", "")}
    s = org.d.get("sandbox") or {}
    if s.get("enabled"):
        return {"secret": s.get("secret", "")}
    return None


def is_sandboxed(org) -> bool:
    return _cfg(org) is not None


_docker_ok: bool | None = None


def docker_available() -> bool:
    """Is a docker CLI on PATH? (cached — the UI disables the sandbox
    checkbox entirely when it isn't; user ruling)"""
    global _docker_ok
    if _docker_ok is None:
        _docker_ok = shutil.which("docker") is not None
    return _docker_ok


def sandbox_secret(org) -> str:
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


def _docker(*args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True,
                          timeout=timeout)


def docker_ok() -> bool:
    try:
        return _docker("version", "--format", "{{.Server.Version}}").returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def ensure_image() -> None:
    if _docker("image", "inspect", IMAGE).returncode == 0:
        return
    with _build_lock:
        if _docker("image", "inspect", IMAGE).returncode == 0:
            return
        r = _docker("build", "-t", IMAGE, os.path.join(REPO_ROOT, "sandbox"),
                    timeout=1200)
        if r.returncode != 0:
            raise RuntimeError("sandbox image build failed: "
                               + (r.stderr or r.stdout)[-500:])


def ensure_container(org) -> str:
    """The org's container, created on first need and restarted if stopped.
    Raises RuntimeError with an actionable message when it cannot run."""
    slug = org.d["slug"]
    k = org.d.get("kiosk") or {}
    name = container_name(slug)
    ins = _docker("container", "inspect", "-f", "{{.State.Running}}", name)
    if ins.returncode == 0:
        if ins.stdout.strip() != "true":
            _docker("start", name)
        return name
    if not docker_ok():
        raise RuntimeError("Docker is not running — start Docker Desktop "
                           "(kiosk sandboxes run their turns in containers)")
    # auth (user ruling): PROXIED SUBSCRIPTION is the default for every kiosk
    # — the container's CLI talks to the bridge's /anthropic passthrough and
    # the HOST attaches the OAuth token; no credential ever enters the
    # sandbox. ORGTREE_SANDBOX_API_KEY remains a hidden escape hatch (a real
    # API key, or 'subscription' to copy the host credentials in).
    key = (k.get("api_key") or os.environ.get("ORGTREE_SANDBOX_API_KEY")
           or "proxied").strip()
    use_proxy = "prox" in key.lower()
    use_sub = key.lower() == "subscription"
    ensure_image()
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
    os.makedirs(ws, exist_ok=True)
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
        IMAGE, "sleep", "infinity", timeout=300)
    if r.returncode != 0:
        raise RuntimeError("sandbox container failed to start: "
                           + (r.stderr or r.stdout)[-500:])
    return name


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


def remove(slug: str) -> None:
    """Org deleted: tear the container down (the sandbox home dir stays on
    disk beside the workspace, same policy as scratch dirs)."""
    try:
        _docker("rm", "-f", container_name(slug), timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        pass


def warm(org) -> None:
    """Fire-and-forget prebuild at kiosk creation so the first turn is not
    minutes slow (image build + container create)."""
    def run():
        try:
            ensure_container(org)
        except Exception as e:              # noqa: BLE001 — surfaced per turn
            print(f"[orgtree] sandbox warm-up for {org.d['slug']!r}: {e}")
    threading.Thread(target=run, daemon=True).start()
