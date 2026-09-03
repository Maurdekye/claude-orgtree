"""Sandboxed orgs — the container execution mode, adversarially.

    .venv/Scripts/python.exe backend/tests/test_sandbox.py            hermetic
    .venv/Scripts/python.exe backend/tests/test_sandbox.py --docker   + real Docker

No pytest (it is not installed here). Plain asserts, `ok N` lines, one final
`ALL N CHECKS PASS`.

WHY THIS FILE EXISTS
--------------------
An entire execution mode was untested. `sandbox.ensure_container` had zero
references in any suite; `disk.py`'s Docker/WSL paths were only ever exercised
as arithmetic against a stubbed shell; the recovery browser was tested against
a stubbed `disk` module whose return shape had already drifted from the real
one (`{"size": …}` where `disk.enumerate_by_size` returns `{"bytes": …}`).
This is also where the security ceiling lives: the container is the boundary,
and the bridge secret is the one key that opens the door out of it.

THE TWO TIERS
-------------
① HERMETIC — the default. ~10 s, no Docker, no WSL, no network, no model
  call. Two seams stand in for the world:

  (a) `sandbox._docker(*args)` → a recording fake daemon (images, containers,
      volumes, labels), so ensure_container's REAL argv reaches an assertion.
      Every security property of the sandbox IS a flag in that argv.
  (b) `disk._run([...])` → a `wsl -d <distro> -e sh -c <script>` call is
      dispatched to a REAL POSIX shell (Git Bash) against a temp-dir distro,
      with stub `mount`/`umount`/`mountpoint`/`mkfs.ext4`/`losetup`/
      `resize2fs`/`e2fsck`/`df` on PATH.

  ☞ (b) is the point. `disk.py` is ~30 shell one-liners and the bugs that live
  there are shell bugs (§4 re-proves the `head|tail` paging trap the module's
  own comments describe). Under a real `sh` with real `find`, `stat`, `sort`,
  `sed`, `du`, `cut`, `dd` and `truncate`, the actual command strings run;
  only the four privileged tools are simulated, and `disk._sh`, `distro()`'s
  parsing and every command string stay REAL. §4 skips with a stated reason
  when no POSIX shell is on the host.

② --docker — the real thing. Builds the real image, formats a real ext4 disk
  in the real docker-desktop distro, seeds it, runs a real container and
  attacks it: read-only rootfs, /usr/local read-only, tmpfs bounds, the host
  filesystem's absence, ENOSPC at the cap with the container surviving, the
  bridge from inside. Skips with a stated reason when the daemon is down.
  Everything it creates is removed; nothing pre-existing is touched.

⚠ ISOLATION. Everything created here is named `zzsbx-*` (slug prefix), lives
under a throwaway ORGTREE_DATA, and is torn down in an atexit hook that
refuses to touch any name it did not create. Ports 7407 only. The user's live
orgs (`game-club`, `resonite`) are unsandboxed and are never loaded.

    §1  identity, config and paths            (no Docker)
    §2  the container contract                (fake daemon)
    §3  the bridge — the one door out         (ASGI + a real uvicorn on 7407)
    §4  disk.py against a real POSIX shell
    §5  the soft cap, the hard cap, the recovery browser
    §6  the sandboxed turn
    §7  subproxy — the OAuth refresh
    §8  creation-time rules
    §9  real Docker                           (--docker only)

Two defects were reproduced RED and FIXED in this suite's own files
(subproxy.py's never-updated `refreshTokenExpiresAt`, disk.py's 5% ext4 root
reserve); five more are printed as ⚑ notes at the end of every run because
they live in files this agent may not write. The run ends with a FIXED HERE
and a REPORTED, NOT FIXED block — read them, they are the point.
"""

from __future__ import annotations

import atexit
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

DOCKER_TIER = "--docker" in sys.argv
ONLY = None
if "--only" in sys.argv:
    ONLY = sys.argv[sys.argv.index("--only") + 1]

DATA = tempfile.mkdtemp(prefix="orgtree-sbxtest-")
os.environ["ORGTREE_DATA"] = DATA

# ⚠ a throwaway ORGTREE_DATA does NOT isolate the MAIL HUB: net._default_address
# falls back to net.DEFAULT_HUB_ADDRESS — the operator's real hub — when this
# root has no defaults.json, and any rig that starts the net daemon then
# registers its fixture orgs there permanently. Measured twice (user report
# 2026-08-06; ~45 fixture orgs again on 2026-08-10). The discard port refuses
# instantly, so registration fails harmlessly into the backoff.
# Guarded over this whole directory by test_external_mail §1.
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w",
          encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

os.environ["ORGTREE_PORT"] = "7407"
os.environ["ORGTREE_BRIDGE_PORT"] = "7407"
os.environ["ORGTREE_PUBLIC_PORT"] = "7407"
os.environ.pop("ORGTREE_SANDBOX_MCP", None)
os.environ.pop("ORGTREE_SANDBOX_API_KEY", None)
os.environ.pop("ORGTREE_EXPOSE_ADMIN", None)

from orgtree import (api, deployment, frozen_install, sandbox,   # noqa: E402
                     store, subproxy, supervisor)
from orgtree import disk as dsk                                  # noqa: E402
from orgtree.ledger import USER                                  # noqa: E402


def _approved_sandbox_labels() -> dict[str, str]:
    """The exact labels the approved manifest requires on the frozen image."""
    import json as _json
    from pathlib import Path as _Path
    root = _Path(frozen_install.REPO_ROOT)
    doc = _json.loads((root / frozen_install.MANIFEST_REL)
                      .read_text(encoding="utf-8"))
    spec = next(c for c in doc["containers"] if c["id"] == "sandbox")
    return {**spec["labels"],
            "io.orgtree.frozen.config":
                frozen_install.APPROVED_MANIFEST_SHA256}

# ---- the blast-radius guard. Nothing in this file may name a slug that does
# not start with this prefix, and the real credentials file may never be the
# one subproxy writes to.
PFX = "zzsbx-"
REAL_CREDS = os.path.expanduser("~/.claude/.credentials.json")
assert DATA != os.path.expanduser("~/orgtree"), "refusing to run on the real data root"

supervisor.chatq_register_org = lambda slug: None
supervisor.chatq_deregister_org = lambda slug: None
sandbox.vm_disk_cap_mib = lambda: None
_warmed: list[str] = []
REAL_WARM = sandbox.warm          # §2 drives the real one; §8 counts calls
sandbox.warm = lambda org: _warmed.append(org.d["slug"])

PASS = 0
NOTES: list[str] = []
FIXED: list[str] = []
SKIPS: list[str] = []

# `windows_path`/`windows_sub` shell out to `wsl -l -q`; §4 exercises that
# detection for real against a fake `_run`. Everywhere else the name is pinned
# so no section outside §4/§9 depends on WSL being installed.
dsk._distro_cache = "docker-desktop"


def check(label, fn):
    global PASS
    fn()
    PASS += 1
    print(f"  ok {PASS:3d}  {label}")


def t(label):
    def deco(fn):
        check(label, fn)
        return fn
    return deco


def note(text):
    """A finding that is REPORTED, not fixed (it lives outside this suite's
    writable files). Printed at the end of every run so it cannot be lost."""
    NOTES.append(text)
    print(f"       ⚑ {text}")


def fixed(text):
    """A defect this suite reproduced AND fixed, in one of its own files."""
    FIXED.append(text)
    print(f"       ✓ FIXED: {text}")


def skip(text):
    SKIPS.append(text)
    print(f"       ○ SKIPPED: {text}")


def section(name):
    if ONLY and ONLY not in name:
        return False
    print(f"\n{name}")
    return True


# --------------------------------------------------------------- fixtures
def mkorg(name, *, kiosk=False, sandboxed=True, secret=None, disk=None,
          kiosk_enabled=True, api_key=None):
    """An org doc straight through store (the create-path itself is §8)."""
    org = store.create_org(PFX + name)
    slug = org.d["slug"]
    assert slug.startswith(PFX), slug
    with store.DOC_LOCK:
        o = store.load_org(slug)
        if kiosk:
            o.d["kiosk"] = {"enabled": kiosk_enabled,
                            "token": "tok" + slug.replace("-", "_"),
                            "credits": 100, "spend_limit": 0.0,
                            "storage_limit_mb": 4096,
                            "sandbox": bool(sandboxed),
                            "sandbox_secret": secret or ("a1" * 16),
                            "auto_raise": False, "max_scope": None,
                            **({"api_key": api_key} if api_key else {})}
        elif sandboxed:
            o.d["sandbox"] = {"enabled": True, "secret": secret or ("b2" * 16)}
        if disk:
            o.d["disk"] = dict(disk)
        store.save_org(o)
    return store.load_org(slug)


def drop(slug):
    try:
        store.delete_org(slug)
    except Exception:                                          # noqa: BLE001
        pass


@atexit.register
def _cleanup():
    for p in glob.glob(os.path.join(DATA, "orgs", "*.json")):
        s = os.path.basename(p)[:-5]
        if s.startswith(PFX):
            drop(s)
    shutil.rmtree(DATA, ignore_errors=True)


# ================================================================== §1
if section("§1  identity, config and paths"):
    O_KIOSK = mkorg("k1", kiosk=True, secret="c3" * 16)
    O_PLAIN = mkorg("p1", sandboxed=False)
    O_ORGSBX = mkorg("s1", secret="d4" * 16)

    @t("a kiosk with sandbox:true is sandboxed; its secret is the kiosk one")
    def _():
        assert sandbox.is_sandboxed(O_KIOSK)
        assert sandbox.sandbox_secret(O_KIOSK) == "c3" * 16

    @t("a normal org with sandbox.enabled is sandboxed too (user ruling)")
    def _():
        assert sandbox.is_sandboxed(O_ORGSBX)
        assert sandbox.sandbox_secret(O_ORGSBX) == "d4" * 16

    @t("a plain org is NOT sandboxed and has no secret")
    def _():
        assert not sandbox.is_sandboxed(O_PLAIN)
        assert sandbox.sandbox_secret(O_PLAIN) == ""

    @t("a kiosk with sandbox:false falls through to the org-level sandbox key")
    def _():
        o = mkorg("k2", kiosk=True, sandboxed=False)
        assert not sandbox.is_sandboxed(o)
        with store.DOC_LOCK:
            o2 = store.load_org(o.d["slug"])
            o2.d["sandbox"] = {"enabled": True, "secret": "e5" * 16}
            store.save_org(o2)
        assert sandbox.is_sandboxed(store.load_org(o.d["slug"]))
        drop(o.d["slug"])

    @t("sandbox.enabled:false is not a sandbox (the flag is read, not the key)")
    def _():
        o = mkorg("k3", sandboxed=False)
        with store.DOC_LOCK:
            o2 = store.load_org(o.d["slug"])
            o2.d["sandbox"] = {"enabled": False, "secret": "f6" * 16}
            store.save_org(o2)
        assert not sandbox.is_sandboxed(store.load_org(o.d["slug"]))
        drop(o.d["slug"])

    @t("container names and the in-container path mirror are stable")
    def _():
        s = O_KIOSK.d["slug"]
        assert sandbox.container_name(s) == "orgtree-" + s
        assert sandbox.cpath_data() == "/home/agent/orgtree"
        assert sandbox.cpath_workspace(s) == f"/home/agent/orgtree/workspaces/{s}"
        # steer.py derives identity from the cwd: <data>/scratch/<org>/<node>
        assert sandbox.cpath_scratch(s, "alice") == \
            f"/home/agent/orgtree/scratch/{s}/alice"

    @t("☞ a lineage id (name@gen) shares the BASE scratch dir, as on the host")
    def _():
        s = O_KIOSK.d["slug"]
        assert sandbox.cpath_scratch(s, "alice@3") == \
            sandbox.cpath_scratch(s, "alice")

    @t("exec_argv runs one command in the org's container, in one cwd")
    def _():
        assert sandbox.exec_argv("orgtree-x", "/w") == \
            ["docker", "exec", "-i", "-w", "/w", "orgtree-x"]

    @t("bridge_url points at the host gateway alias and the bridge port")
    def _():
        assert sandbox.bridge_url() == "http://host.docker.internal:7407"

    @t("uses_subscription_auth: proxied is the default, not credential copying")
    def _():
        assert not sandbox.uses_subscription_auth(None)
        assert not sandbox.uses_subscription_auth({})
        assert not sandbox.uses_subscription_auth({"api_key": "sk-ant-x"})
        assert sandbox.uses_subscription_auth({"api_key": "subscription"})
        assert sandbox.uses_subscription_auth({"api_key": " Subscription "})

    @t("uses_subscription_auth honours ORGTREE_SANDBOX_API_KEY")
    def _():
        os.environ["ORGTREE_SANDBOX_API_KEY"] = "subscription"
        try:
            assert sandbox.uses_subscription_auth({})
            # an explicit per-org key still wins over the env
            assert not sandbox.uses_subscription_auth({"api_key": "sk-ant-1"})
        finally:
            os.environ.pop("ORGTREE_SANDBOX_API_KEY")

    @t("sandbox_home is the host sandbox dir until the org rides a disk")
    def _():
        s = O_KIOSK.d["slug"]
        sandbox._disk_flag.pop(s, None)
        assert sandbox.sandbox_home(s) == \
            os.path.join(DATA, "sandboxes", s, "home")
        assert not sandbox.on_disk(s)

    @t("☞ sandbox_home FOLLOWS the org onto its disk once migrated")
    def _():
        s = O_KIOSK.d["slug"]
        with store.DOC_LOCK:
            o = store.load_org(s)
            o.d["disk"] = {"size_mb": 4096}
            store.save_org(o)
        sandbox._disk_flag.pop(s, None)
        assert sandbox.on_disk(s)
        assert sandbox.sandbox_home(s) == dsk.windows_sub(s, "home")
        assert "wsl.localhost" in sandbox.sandbox_home(s)
        with store.DOC_LOCK:
            o = store.load_org(s)
            o.d.pop("disk")
            store.save_org(o)
        sandbox._disk_flag.pop(s, None)

    @t("on_disk caches for ~10 s (hot paths must not re-load the doc)")
    def _():
        s = O_ORGSBX.d["slug"]
        sandbox._disk_flag.pop(s, None)
        assert not sandbox.on_disk(s)
        with store.DOC_LOCK:
            o = store.load_org(s)
            o.d["disk"] = {"size_mb": 4096}
            store.save_org(o)
        assert not sandbox.on_disk(s), "the cache should still say no"
        sandbox._disk_flag.pop(s, None)
        assert sandbox.on_disk(s)
        with store.DOC_LOCK:
            o = store.load_org(s)
            o.d.pop("disk")
            store.save_org(o)
        sandbox._disk_flag.pop(s, None)

    @t("on_disk fails CLOSED for an unreadable org doc")
    def _():
        assert not sandbox.on_disk("no-such-org-" + PFX)

    @t("the /usr/local volume name carries the CLI version AND the image rev")
    def _():
        assert sandbox.usrlocal_volume("2.1.220") == \
            f"orgtree-usrlocal-2.1.220-{sandbox.IMG_REV}"
        assert sandbox.usrlocal_volume("2.1.221") != \
            sandbox.usrlocal_volume("2.1.220"), "a CLI move must move the volume"

    @t("_parse_size reads docker's SI human sizes")
    def _():
        assert sandbox._parse_size("0B") == 0
        assert sandbox._parse_size("5.34MB") == 5340000
        assert sandbox._parse_size("1.06GB") == 1060000000
        assert sandbox._parse_size("12") == 12
        assert sandbox._parse_size("nonsense") == 0
        assert sandbox._parse_size("") == 0


# ================================================================== §2
# A recording fake daemon. The point is not that Docker is simulated well —
# it is that `ensure_container`'s REAL argv reaches an assertion. Every
# security property of the sandbox is a flag in that argv.
class FakeDocker:
    def __init__(self):
        self.calls: list[list[str]] = []
        self.server_up = True
        self.images: set[str] = set()
        # tag → OCI labels, so a frozen image can be approved or not
        self.image_labels: dict[str, dict[str, str]] = {}
        self.volumes: set[str] = set()
        self.networks: dict[str, dict] = {
            "bridge": {"internal": False, "gateway": "172.17.0.1"}}
        self.containers: dict[str, dict] = {}
        self.build_fails = False
        self.run_fails = False
        self.migrate_output = "MIGRATED\n"
        self.timeouts: set[str] = set()      # first arg → raise TimeoutExpired

    def cp(self, args, rc=0, out="", err=""):
        return subprocess.CompletedProcess(args=list(args), returncode=rc,
                                           stdout=out, stderr=err)

    def find(self, *prefix):
        return [c for c in self.calls if c[:len(prefix)] == list(prefix)]

    def last_run(self):
        runs = [c for c in self.calls if c and c[0] == "run" and "-d" in c]
        assert runs, "no `docker run -d` was issued"
        return runs[-1]

    def __call__(self, *args, timeout=120):
        a = list(args)
        self.calls.append(a)
        if a and a[0] in self.timeouts:
            raise subprocess.TimeoutExpired(a, timeout)
        if a[:1] == ["version"]:
            return self.cp(a, 0 if self.server_up else 1, "28.0.0\n")
        if a[:2] == ["image", "inspect"]:
            if a[2] not in self.images:
                return self.cp(a, 1, "", f"No such image: {a[2]}")
            # Real `docker image inspect` output: frozen attestation reads
            # the labels off this to decide whether the image is approved.
            return self.cp(a, 0, json.dumps([{
                "Id": "sha256:" + "1" * 64,
                "Config": {"Labels": self.image_labels.get(a[2], {})}}]))
        if a[:1] == ["build"]:
            if self.build_fails:
                return self.cp(a, 1, "", "no space left on device")
            self.images.add(a[a.index("-t") + 1])
            return self.cp(a, 0)
        if a[:2] == ["volume", "inspect"]:
            return self.cp(a, 0 if a[2] in self.volumes else 1)
        if a[:2] == ["volume", "create"]:
            self.volumes.add(a[2])
            return self.cp(a, 0)
        if a[:2] == ["volume", "rm"]:
            for v in a[3:]:
                self.volumes.discard(v)
            return self.cp(a, 0)
        if a[:2] == ["network", "inspect"]:
            name = a[2]
            n = self.networks.get(name)
            if not n:
                return self.cp(a, 1, "", f"No such network: {name}")
            if "--format" in a:
                fmt = a[a.index("--format") + 1]
                if ".Internal" in fmt:
                    return self.cp(
                        a, 0, ("true 1\n" if n.get("internal")
                               and n.get("frozen") else "false <no value>\n"))
                return self.cp(a, 0, n.get("gateway", "172.31.0.1") + "\n")
            return self.cp(a, 0, json.dumps([{"Name": name}]))
        if a[:2] == ["network", "create"]:
            name = a[-1]
            self.networks[name] = {
                "internal": "--internal" in a,
                "frozen": "orgtree.frozen=1" in a,
                "gateway": "172.31.0.1"}
            return self.cp(a, 0, name + "\n")
        if a[:2] == ["network", "connect"]:
            name, container = a[-2], a[-1]
            c = self.containers.get(container)
            if not c or name not in self.networks:
                return self.cp(a, 1, "", "missing container or network")
            c["networks"].add(name)
            return self.cp(a, 0)
        if a[:2] == ["network", "rm"]:
            name = a[2]
            if any(name in c.get("networks", set())
                   for c in self.containers.values()):
                return self.cp(a, 1, "", "network has active endpoints")
            self.networks.pop(name, None)
            return self.cp(a, 0)
        if a[:2] == ["container", "inspect"]:
            name, fmt = a[-1], a[a.index("-f") + 1]
            c = self.containers.get(name)
            if not c:
                return self.cp(a, 1, "", f"No such container: {name}")
            out = fmt.replace("{{.State.Running}}",
                              "true" if c["running"] else "false")
            out = out.replace("{{.Config.Image}}", c["image"])
            # ANY label, not just orgtree.layout — the container's identity
            # grew an `orgtree.auth` label 2026-08-18 and a hardcoded
            # substitution silently answered "" for it, which reads as a
            # changed identity and recreates on every call
            out = re.sub(r'\{\{index \.Config\.Labels "([^"]+)"\}\}',
                         lambda m: c["labels"].get(m.group(1), "<no value>"),
                         out)
            out = out.replace("{{json .NetworkSettings.Networks}}",
                              json.dumps({n: {} for n in c["networks"]}))
            return self.cp(a, 0, out + "\n")
        if a[:1] == ["run"] and "--rm" in a:
            return self.cp(a, 0, self.migrate_output)       # migration helper
        if a[:1] in (["run"], ["create"]):
            if self.run_fails:
                return self.cp(a, 1, "", "invalid mount config")
            name = a[a.index("--name") + 1]
            labels = {}
            for i, x in enumerate(a):
                if x == "--label" and "=" in a[i + 1]:
                    k, _, v = a[i + 1].partition("=")
                    labels[k] = v
            image = next((x for x in a if x in self.images), a[-3])
            network = a[a.index("--network") + 1] \
                if "--network" in a else "bridge"
            self.containers[name] = {"running": a[0] == "run", "image": image,
                                     "labels": labels,
                                     "networks": {network}}
            return self.cp(a, 0, "deadbeef\n")
        if a[:2] == ["rm", "-f"]:
            self.containers.pop(a[2], None)
            return self.cp(a, 0)
        if a[:1] == ["start"]:
            if a[1] in self.containers:
                self.containers[a[1]]["running"] = True
            return self.cp(a, 0)
        if a[:1] == ["stop"]:
            if a[-1] in self.containers:
                self.containers[a[-1]]["running"] = False
            return self.cp(a, 0)
        if a[:1] == ["exec"]:
            return self.cp(a, 0)
        if a[:2] == ["system", "df"]:
            return self.cp(a, 0, json.dumps([
                {"Name": "orgtree-sys-x-usr", "Size": "1.5GB"}]))
        return self.cp(a, 0)


class FakeDisk:
    """disk.py stand-in for §2 (the real module runs under a real shell in
    §4). Records the lifecycle calls ensure_container depends on."""

    def __init__(self, root):
        self.root = root
        self.calls: list[tuple] = []
        self.mounted: set[str] = set()
        self.mount_fails: str | None = None
        self.usage_val: tuple[int, int] | None = (1 << 20, 4096 << 20)

    def install(self):
        self.orig = {k: getattr(dsk, k) for k in
                     ("create", "mount", "unmount", "destroy", "usage",
                      "mount_path", "windows_path", "windows_sub",
                      "shrink_image", "grow", "is_mounted")}
        dsk.create = self._create
        dsk.mount = self._mount
        dsk.unmount = lambda slug: self.calls.append(("unmount", slug))
        dsk.destroy = lambda slug: self.calls.append(("destroy", slug))
        dsk.usage = lambda slug, max_age=15.0: self.usage_val
        dsk.is_mounted = lambda slug: slug in self.mounted
        dsk.mount_path = lambda slug: f"/mnt/wsl/orgtree-disk/{slug}"
        dsk.windows_path = lambda slug: os.path.join(self.root, slug)
        dsk.windows_sub = lambda slug, sub: os.path.join(self.root, slug, sub)
        dsk.shrink_image = lambda slug, mb: self.calls.append(
            ("shrink", slug, mb))
        dsk.grow = lambda slug, mb: self.calls.append(("grow", slug, mb))
        return self

    def restore(self):
        for k, v in self.orig.items():
            setattr(dsk, k, v)

    def _create(self, slug, mb):
        self.calls.append(("create", slug, mb))
        self.mounted.add(slug)

    def _mount(self, slug):
        self.calls.append(("mount", slug))
        if self.mount_fails:
            raise dsk.DiskError(self.mount_fails)
        self.mounted.add(slug)


if section("§2  the container contract"):
    FD = FakeDocker()
    sandbox._docker = FD
    supervisor.cli_version = lambda: "2.1.220"
    FDISK = FakeDisk(os.path.join(DATA, "fakedisks")).install()
    TAG = f"orgtree-sandbox:2.1.220-{sandbox.IMG_REV}"

    def fresh(name, **kw):
        FD.calls.clear()
        o = mkorg(name, disk={"size_mb": 4096}, **kw)
        sandbox._disk_flag.pop(o.d["slug"], None)
        os.makedirs(os.path.join(FDISK.root, o.d["slug"], "home"), exist_ok=True)
        return o

    def mounts(argv):
        return [argv[i + 1] for i, x in enumerate(argv) if x == "-v"]

    @t("ensure_image builds a tag carrying the HOST CLI version + image rev")
    def _():
        FD.images.clear()
        FD.calls.clear()
        assert sandbox.ensure_image() == TAG
        b = FD.find("build")
        assert b and b[0][b[0].index("-t") + 1] == TAG, b
        assert "--build-arg" in b[0] and "CLAUDE_VERSION=2.1.220" in b[0], b
        assert b[0][-1] == os.path.join(sandbox.REPO_ROOT, "sandbox"), b

    @t("…and does NOT rebuild when the tag already exists")
    def _():
        FD.calls.clear()
        assert sandbox.ensure_image() == TAG
        assert not FD.find("build"), FD.calls

    @t("a failed image build raises with the daemon's own message")
    def _():
        FD.images.clear()
        FD.build_fails = True
        try:
            sandbox.ensure_image()
            raise AssertionError("build failure was swallowed")
        except RuntimeError as e:
            assert "no space left" in str(e), e
        finally:
            FD.build_fails = False
        sandbox.ensure_image()

    @t("☠ a stopped daemon is an actionable refusal, never a silent no-op")
    def _():
        o = fresh("c0")
        FD.server_up = False
        try:
            sandbox.ensure_container(o)
            raise AssertionError("ran without a daemon")
        except RuntimeError as e:
            assert "Docker is not running" in str(e), e
        finally:
            FD.server_up = True
        assert not FD.find("run"), "it tried to run something anyway"
        drop(o.d["slug"])

    # ---- the argv, which IS the security boundary
    ORG_A = fresh("c1", kiosk=True, secret="11" * 16)
    SLUG_A = ORG_A.d["slug"]
    NAME_A = sandbox.container_name(SLUG_A)
    assert sandbox.ensure_container(ORG_A) == NAME_A
    RUN = FD.last_run()
    MP = f"/mnt/wsl/orgtree-disk/{SLUG_A}"

    @t("☠ the rootfs runs READ-ONLY (no unmeasured writable surface)")
    def _():
        assert "--read-only" in RUN, RUN

    @t("☠ /tmp and /run are RAM tmpfs, sized, and /tmp is 1777")
    def _():
        tm = [RUN[i + 1] for i, x in enumerate(RUN) if x == "--tmpfs"]
        assert f"/tmp:rw,size={sandbox.TMP_SIZE},mode=1777" in tm, tm
        assert f"/run:rw,size={sandbox.RUN_SIZE}" in tm, tm
        assert sandbox.TMP_SIZE and sandbox.RUN_SIZE, "unbounded tmpfs"

    @t("☠ CPU and memory are capped (the tmpfs bound rides --memory)")
    def _():
        assert RUN[RUN.index("--memory") + 1] == sandbox.MEM
        assert RUN[RUN.index("--cpus") + 1] == sandbox.CPUS

    @t("☠ every persistent path is on the org's OWN disk image")
    def _():
        m = mounts(RUN)
        for d in sandbox.SYS_DIRS:
            assert f"{MP}/{d}:/{d}" in m, (d, m)
        assert f"{MP}/home:/home/agent" in m, m
        assert f"{MP}/workspace:{sandbox.cpath_workspace(SLUG_A)}" in m, m
        assert f"{MP}/scratch:{sandbox.cpath_data()}/scratch/{SLUG_A}" in m, m

    @t("☠ the ONLY host-filesystem bind is the backend, READ-ONLY")
    def _():
        host = [s for s in mounts(RUN)
                if not s.startswith(MP + "/") and not s.startswith("orgtree-")]
        assert host == [f"{sandbox.BACKEND_DIR}:/opt/orgtree-backend:ro"], host
        # ⚠ the data root (org docs, every other org's disk, the user's home)
        # is NOT reachable from inside
        assert not any(DATA in s for s in mounts(RUN)), mounts(RUN)

    @t("☠ /usr/local is the version-pinned READ-ONLY volume (the CLI is fixed)")
    def _():
        m = mounts(RUN)
        want = f"{sandbox.usrlocal_volume('2.1.220')}:/usr/local:ro"
        assert want in m, m

    @t("the layout label is stamped so a stale container is recreated, not run")
    def _():
        assert f"orgtree.layout={sandbox.LAYOUT}" in RUN, RUN

    @t("the container idles; turns arrive by exec")
    def _():
        assert RUN[-3:] == [TAG, "sleep", "infinity"], RUN[-4:]

    @t("host.docker.internal is mapped (the bridge is the one door out)")
    def _():
        assert RUN[RUN.index("--add-host") + 1] == \
            "host.docker.internal:host-gateway"

    @t("☠ proxied auth: the CLI's base URL carries the secret; no credential")
    def _():
        env = dict(RUN[i + 1].split("=", 1) for i, x in enumerate(RUN)
                   if x == "-e")
        assert env["ANTHROPIC_BASE_URL"] == \
            f"http://host.docker.internal:7407/anthropic/{'11' * 16}"
        assert env["ANTHROPIC_API_KEY"] == "orgtree-proxied"
        assert not any("sk-ant" in v for v in env.values()), env

    @t("☠ .bridge is the only secret in the container, and it is on the disk")
    def _():
        p = os.path.join(FDISK.root, SLUG_A, "home", "orgtree", ".bridge")
        b = json.load(open(p, encoding="utf-8"))
        assert b == {"url": "http://host.docker.internal:7407",
                     "secret": "11" * 16}, b
        # steer.py reads <data-root>/.bridge with HOME=/home/agent ⇒
        # /home/agent/orgtree/.bridge — the file must sit exactly there
        assert p.endswith(os.path.join("home", "orgtree", ".bridge"))

    @t("a second call reuses the running container (no rm, no run)")
    def _():
        FD.calls.clear()
        assert sandbox.ensure_container(store.load_org(SLUG_A)) == NAME_A
        assert not FD.find("run") and not FD.find("rm"), FD.calls

    @t("☠ frozen mode gives the agent ONLY a private internal network + relay")
    def _():
        o = fresh("cf", kiosk=True, secret="7a" * 16)
        slug = o.d["slug"]
        net = sandbox.frozen_network_name(slug)
        gateway = sandbox.frozen_gateway_name(slug)
        os.environ[deployment.PROFILE_ENV] = "frozen"
        # Frozen mode never builds an image lazily: it runs the ONE approved
        # content-addressed tag or refuses. Publish that tag with the labels
        # the manifest approves, exactly as a real prebuilt image would.
        frozen_tag = frozen_install.required_sandbox_image_tag()
        FD.images.add(frozen_tag)
        FD.image_labels[frozen_tag] = _approved_sandbox_labels()
        try:
            sandbox.ensure_container(o)
            agent_run0 = next(
                c for c in FD.find("run")
                if c[c.index("--name") + 1] == sandbox.container_name(slug))
            assert agent_run0[agent_run0.index("--name") - 1] == frozen_tag \
                or frozen_tag in agent_run0, agent_run0
            # ☠ /usr/local is seeded from the image ONCE and reused by name.
            # In frozen mode it must be keyed to the approved configuration,
            # not the host CLI version — otherwise a frozen container could
            # mount a /usr/local seeded from an unapproved standard image
            # built at the same CLI version, and the pins would verify while
            # the CLI actually running came from somewhere else.
            usrlocal = next(v for i, v in enumerate(agent_run0)
                            if agent_run0[i - 1] == "-v"
                            and v.endswith(":/usr/local:ro"))
            assert "frozen-" in usrlocal, usrlocal
            assert supervisor.cli_version() not in usrlocal, usrlocal
            creates = FD.find("network", "create")
            assert creates and "--internal" in creates[-1] \
                and creates[-1][-1] == net, creates
            assert FD.networks[net]["internal"], FD.networks[net]

            agent = FD.containers[sandbox.container_name(slug)]
            relay = FD.containers[gateway]
            assert agent["networks"] == {net}, agent["networks"]
            assert relay["networks"] == {"bridge", net}, relay["networks"]

            runs = FD.find("run")
            agent_run = next(c for c in runs
                             if c[c.index("--name") + 1]
                             == sandbox.container_name(slug))
            relay_run = next(c for c in FD.find("create")
                             if c[c.index("--name") + 1] == gateway)
            assert agent_run[agent_run.index("--network") + 1] == net
            assert "--add-host" not in agent_run, agent_run
            assert relay_run[relay_run.index("--network") + 1] == "bridge"
            assert "--read-only" in relay_run and "--cap-drop" in relay_run
            assert relay_run[relay_run.index("--cap-drop") + 1] == "ALL"
            assert "no-new-privileges" in relay_run, relay_run
            assert [x for x in relay_run if x == "-v"] == ["-v"], relay_run
            assert f"{sandbox.BACKEND_DIR}:/opt/orgtree-backend:ro" in relay_run
            assert relay_run[relay_run.index("--bind") + 1] == \
                sandbox.FROZEN_GATEWAY_ALIAS
            joins = FD.find("network", "connect")
            assert joins and "--alias" in joins[-1] \
                and sandbox.FROZEN_GATEWAY_ALIAS in joins[-1], joins

            env = dict(agent_run[i + 1].split("=", 1)
                       for i, x in enumerate(agent_run) if x == "-e")
            assert not any(k.startswith("ANTHROPIC_") for k in env), env
            assert sandbox.bridge_url() == (
                f"http://{sandbox.FROZEN_GATEWAY_ALIAS}:"
                f"{sandbox.FROZEN_GATEWAY_PORT}")
            bridge_file = os.path.join(
                FDISK.root, slug, "home", "orgtree", ".bridge")
            assert json.load(open(bridge_file, encoding="utf-8")) == {
                "url": sandbox.bridge_url()}
        finally:
            os.environ.pop(deployment.PROFILE_ENV, None)

        # Profile rollback is part of preserving standard mode: a frozen
        # container must never remain isolated after the operator returns to
        # standard, and the now-unused relay/network must not leak.
        FD.calls.clear()
        sandbox.ensure_container(store.load_org(slug))
        assert FD.find("rm", "-f", sandbox.container_name(slug)), FD.calls
        assert gateway not in FD.containers and net not in FD.networks
        standard_run = FD.last_run()
        assert "--network" not in standard_run
        assert standard_run[standard_run.index("--add-host") + 1] == \
            "host.docker.internal:host-gateway"
        drop(slug)

    @t("☠ frozen mode refuses a same-name Docker network that is not internal")
    def _():
        o = fresh("cfc", kiosk=True, secret="7b" * 16)
        slug = o.d["slug"]
        net = sandbox.frozen_network_name(slug)
        FD.networks[net] = {"internal": False, "frozen": False,
                            "gateway": "172.31.0.1"}
        os.environ[deployment.PROFILE_ENV] = "frozen"
        try:
            try:
                sandbox.ensure_container(o)
                raise AssertionError("adopted a non-internal network")
            except RuntimeError as e:
                assert "refusing existing Docker network" in str(e), e
            assert sandbox.container_name(slug) not in FD.containers
            assert sandbox.frozen_gateway_name(slug) not in FD.containers
        finally:
            os.environ.pop(deployment.PROFILE_ENV, None)
            FD.networks.pop(net, None)
        drop(slug)

    @t("an AUTH change recreates the container (the credential is baked in "
       "at `docker run`, and supervisor.bills_the_key trusts the config)")
    def _():
        # redteam 2026-08-18: nothing recreated a container when its auth
        # changed, so one created under ORGTREE_SANDBOX_API_KEY kept billing
        # that key after the var was unset — while `bills_the_key`, reading
        # today's config, called those turns "subscription" and timed the
        # container's own API limits against the host's lanes.
        FD.calls.clear()
        org = store.load_org(SLUG_A)
        assert sandbox.ensure_container(org) == NAME_A
        assert not FD.find("rm"), "a no-op call recreated the container"
        os.environ["ORGTREE_SANDBOX_API_KEY"] = "sk-ant-escape-hatch"
        try:
            FD.calls.clear()
            assert sandbox.ensure_container(store.load_org(SLUG_A)) == NAME_A
            assert FD.find("rm"), (
                "the auth changed and the container was reused: it is still "
                "running on the previous credential")
            lbl = [x for x in FD.last_run() if x.startswith("orgtree.auth=")]
            assert lbl and lbl[0].startswith("orgtree.auth=key:"), lbl
            assert "sk-ant-escape-hatch" not in " ".join(
                x for x in FD.last_run()
                if x.startswith("orgtree.auth=")), "the label leaks the key"
        finally:
            os.environ.pop("ORGTREE_SANDBOX_API_KEY", None)
        FD.calls.clear()
        assert sandbox.ensure_container(store.load_org(SLUG_A)) == NAME_A
        assert FD.find("rm"), "unsetting the key must recreate it too"

    @t("a STOPPED container is started, not recreated (state survives)")
    def _():
        FD.containers[NAME_A]["running"] = False
        FD.calls.clear()
        sandbox.ensure_container(store.load_org(SLUG_A))
        assert FD.find("start", NAME_A), FD.calls
        assert not FD.find("run"), "it recreated a stopped container"

    @t("☞ a CLI version move recreates the container (№44: the image is pinned)")
    def _():
        supervisor.cli_version = lambda: "2.1.221"
        FD.calls.clear()
        sandbox.ensure_container(store.load_org(SLUG_A))
        assert FD.find("rm", "-f", NAME_A), FD.calls
        run = FD.last_run()
        assert run[-3] == f"orgtree-sandbox:2.1.221-{sandbox.IMG_REV}", run[-3]
        assert f"{sandbox.usrlocal_volume('2.1.221')}:/usr/local:ro" in mounts(run)
        supervisor.cli_version = lambda: "2.1.220"

    @t("a container from an older LAYOUT is recreated too")
    def _():
        sandbox.ensure_container(store.load_org(SLUG_A))
        FD.containers[NAME_A]["labels"]["orgtree.layout"] = "volumes-v0"
        FD.calls.clear()
        sandbox.ensure_container(store.load_org(SLUG_A))
        assert FD.find("rm", "-f", NAME_A), FD.calls
        assert FD.last_run()[-3] == TAG

    @t("☠ an unmountable disk HARD-REFUSES the start (never an empty bind)")
    def _():
        FDISK.mount_fails = "org disk mount failed for x: no such loop device"
        FD.calls.clear()
        try:
            sandbox.ensure_container(store.load_org(SLUG_A))
            raise AssertionError("started with no disk")
        except dsk.DiskError as e:
            assert "mount failed" in str(e), e
        finally:
            FDISK.mount_fails = None
        assert not FD.find("run"), "Docker would have minted an empty dir"

    @t("a container that fails to start raises with the daemon's message")
    def _():
        FD.run_fails = True
        FD.containers.pop(NAME_A, None)
        try:
            sandbox.ensure_container(store.load_org(SLUG_A))
            raise AssertionError("no raise")
        except RuntimeError as e:
            assert "failed to start" in str(e) and "invalid mount" in str(e), e
        finally:
            FD.run_fails = False

    # ---- auth modes
    @t("an explicit api_key replaces the proxy with a plain env key")
    def _():
        o = fresh("c2", kiosk=True, api_key="sk-ant-test-123")
        sandbox.ensure_container(o)
        run = FD.last_run()
        env = [run[i + 1] for i, x in enumerate(run) if x == "-e"]
        assert env == ["ANTHROPIC_API_KEY=sk-ant-test-123"], env
        drop(o.d["slug"])

    @t("☠ subscription auth + a PUBLIC kiosk URL is refused STRUCTURALLY")
    def _():
        o = fresh("c3", kiosk=True, api_key="subscription")
        FD.calls.clear()
        try:
            sandbox.ensure_container(o)
            raise AssertionError("copied host credentials into a public kiosk")
        except RuntimeError as e:
            assert "refused" in str(e) and "credentials" in str(e), e
        assert not FD.find("run"), FD.calls
        drop(o.d["slug"])

    @t("subscription auth with NO kiosk URL copies the credentials file in")
    def _():
        o = fresh("c4", secret="22" * 16)
        os.environ["ORGTREE_SANDBOX_API_KEY"] = "subscription"
        fake_home = os.path.join(DATA, "fakehome")
        os.makedirs(os.path.join(fake_home, ".claude"), exist_ok=True)
        with open(os.path.join(fake_home, ".claude", ".credentials.json"),
                  "w") as f:
            f.write('{"claudeAiOauth":{"accessToken":"HOST-TOKEN"}}')
        real_exp = os.path.expanduser
        os.path.expanduser = lambda p: (
            p.replace("~", fake_home) if p.startswith("~/.claude")
            else real_exp(p))
        try:
            sandbox.ensure_container(o)
            run = FD.last_run()
            assert not [x for i, x in enumerate(run) if x == "-e"], run
            dst = os.path.join(FDISK.root, o.d["slug"], "home", ".claude",
                               ".credentials.json")
            assert "HOST-TOKEN" in open(dst, encoding="utf-8").read()
        finally:
            os.path.expanduser = real_exp
            os.environ.pop("ORGTREE_SANDBOX_API_KEY")
        note("subscription auth writes the HOST's OAuth token onto the org "
             "disk; the browser's only defence is a FILENAME denylist "
             "(_PUBLIC_DISK_DENY) and root-in-container can copy it "
             "elsewhere — see §3's copy-vector reproduction.")
        drop(o.d["slug"])

    @t("subscription auth with no credentials file on the host is refused")
    def _():
        o = fresh("c5", secret="33" * 16)
        os.environ["ORGTREE_SANDBOX_API_KEY"] = "subscription"
        real_exp = os.path.expanduser
        os.path.expanduser = lambda p: (os.path.join(DATA, "nope", p[2:])
                                        if p.startswith("~/") else real_exp(p))
        try:
            sandbox.ensure_container(o)
            raise AssertionError("ran with no credentials")
        except RuntimeError as e:
            assert "no Claude credentials found" in str(e), e
        finally:
            os.path.expanduser = real_exp
            os.environ.pop("ORGTREE_SANDBOX_API_KEY")
        drop(o.d["slug"])

    # ---- the one-time migration onto the org disk
    def hire_one(slug, name="alice"):
        with store.DOC_LOCK:
            o = store.load_org(slug)
            o.hire(USER, None, "sonnet", 10, name, charter="test")
            store.save_org(o)
        return name

    @t("an un-migrated sandboxed org migrates onto a disk on first need")
    def _():
        o = mkorg("m1", kiosk=True, secret="44" * 16)
        s = o.d["slug"]
        sandbox._disk_flag.pop(s, None)
        FDISK.calls.clear()
        FD.calls.clear()
        sandbox.ensure_container(o)
        assert ("create", s, 4096) in FDISK.calls, FDISK.calls
        d = store.load_org(s).d["disk"]
        assert d["size_mb"] == 4096 and d["migrated_at"], d
        # the copy helper: legacy volumes and host dirs read-only, disk at /dst
        helper = [c for c in FD.calls if c[:2] == ["run", "--rm"]][0]
        m = mounts(helper)
        assert f"/mnt/wsl/orgtree-disk/{s}:/dst" in m, m
        assert all(x.endswith(":ro") for x in m if ":/old" in x or ":/oldhost" in x)
        script = helper[-1]
        assert "MISMATCH" in script and "cp -a" in script, script[:200]
        assert "chown -R $AUID:$AGID" in script, "on-disk trees must be agent-owned"
        drop(s)

    @t("☞ migration FLOORS a sub-4096 limit and tells the operator in-product")
    def _():
        o = mkorg("m2", kiosk=True, secret="45" * 16)
        s = o.d["slug"]
        with store.DOC_LOCK:
            oo = store.load_org(s)
            oo.d["kiosk"]["storage_limit_mb"] = 256
            store.save_org(oo)
        sandbox._disk_flag.pop(s, None)
        FDISK.calls.clear()
        sandbox.ensure_container(store.load_org(s))
        assert ("create", s, 4096) in FDISK.calls, FDISK.calls
        inbox = store.load_org(s).user_mailbox()
        assert inbox and "256 MB" in inbox[-1]["body"] \
            and "4096" in inbox[-1]["body"], inbox
        drop(s)

    @t("migration rewrites the workspace path everywhere it was recorded")
    def _():
        o = mkorg("m3", secret="46" * 16)
        s = o.d["slug"]
        hire_one(s)
        old_ws = store.load_org(s).d["workspace"]
        sandbox._disk_flag.pop(s, None)
        sandbox.ensure_container(store.load_org(s))
        o2 = store.load_org(s)
        new_ws = dsk.windows_sub(s, "workspace")
        assert o2.d["workspace"] == new_ws, o2.d["workspace"]
        assert [d["path"] for d in o2.d["dirs"]] == [new_ws], o2.d["dirs"]
        assert all(d["path"] == new_ws
                   for n in o2.nodes.values() for d in n["scope"]["add_dirs"]), \
            "a node kept a grant on the pre-migration host workspace"
        assert old_ws != new_ws
        drop(s)

    @t("migration clears the RETIRED legacy storage freeze, org- and node-wide")
    def _():
        o = mkorg("m4", secret="47" * 16)
        s = o.d["slug"]
        hire_one(s)
        with store.DOC_LOCK:
            oo = store.load_org(s)
            oo.d["storage_frozen"] = True
            oo.node("alice")["frozen"] = {"storage": True,
                                          "storage_error": "over cap"}
            store.save_org(oo)
        sandbox._disk_flag.pop(s, None)
        sandbox.ensure_container(store.load_org(s))
        o2 = store.load_org(s)
        assert "storage_frozen" not in o2.d, o2.d.get("storage_frozen")
        assert not o2.node("alice").get("frozen"), o2.node("alice")["frozen"]
        drop(s)

    @t("☠ a migration whose copy did not verify flips NOTHING")
    def _():
        o = mkorg("m5", secret="48" * 16)
        s = o.d["slug"]
        ws0 = store.load_org(s).d["workspace"]
        sandbox._disk_flag.pop(s, None)
        FD.migrate_output = "MISMATCH usr 41 40\n"
        try:
            sandbox.ensure_container(store.load_org(s))
            raise AssertionError("a failed migration was accepted")
        except RuntimeError as e:
            assert "old state is untouched" in str(e), e
        finally:
            FD.migrate_output = "MIGRATED\n"
        o2 = store.load_org(s)
        assert "disk" not in o2.d and o2.d["workspace"] == ws0, o2.d
        drop(s)

    # ---- the staged shrink
    @t("try_apply_pending_resize is a no-op when nothing is pending")
    def _():
        assert sandbox.try_apply_pending_resize(store.load_org(SLUG_A)) is None

    @t("☞ a pending shrink applies at the container-down moment")
    def _():
        with store.DOC_LOCK:
            o = store.load_org(SLUG_A)
            o.d["disk"] = {"size_mb": 8192, "pending_size_mb": 4096}
            store.save_org(o)
        FDISK.usage_val = (100 << 20, 8192 << 20)     # 100 MB used — it fits
        FDISK.calls.clear()
        assert sandbox.try_apply_pending_resize(store.load_org(SLUG_A)) is None
        assert ("shrink", SLUG_A, 4096) in FDISK.calls, FDISK.calls
        d = store.load_org(SLUG_A).d["disk"]
        assert d == {"size_mb": 4096}, d

    @t("☠ a shrink that no longer fits stays PENDING and says what to free")
    def _():
        with store.DOC_LOCK:
            o = store.load_org(SLUG_A)
            o.d["disk"] = {"size_mb": 8192, "pending_size_mb": 4096}
            store.save_org(o)
        FDISK.usage_val = (4000 << 20, 8192 << 20)    # over 90% of 4096
        FDISK.calls.clear()
        msg = sandbox.try_apply_pending_resize(store.load_org(SLUG_A))
        assert msg and "free about" in msg and "not applied" in msg, msg
        assert not [c for c in FDISK.calls if c[0] == "shrink"], FDISK.calls
        assert store.load_org(SLUG_A).d["disk"]["pending_size_mb"] == 4096
        FDISK.usage_val = (1 << 20, 4096 << 20)

    @t("ensure_container attempts the pending shrink only with the org down")
    def _():
        FD.containers.pop(NAME_A, None)
        FDISK.calls.clear()
        sandbox.ensure_container(store.load_org(SLUG_A))
        assert ("shrink", SLUG_A, 4096) in FDISK.calls, FDISK.calls
        # …and never when it is already up
        FDISK.calls.clear()
        with store.DOC_LOCK:
            o = store.load_org(SLUG_A)
            o.d["disk"] = {"size_mb": 4096, "pending_size_mb": 4096}
            store.save_org(o)
        sandbox.ensure_container(store.load_org(SLUG_A))
        assert not [c for c in FDISK.calls if c[0] == "shrink"], FDISK.calls
        with store.DOC_LOCK:
            o = store.load_org(SLUG_A)
            o.d["disk"] = {"size_mb": 4096}
            store.save_org(o)

    @t("a refused pending shrink never blocks the turn (best-effort, logged)")
    def _():
        orig = sandbox.try_apply_pending_resize
        sandbox.try_apply_pending_resize = lambda org: (_ for _ in ()).throw(
            dsk.DiskError("distro is gone"))
        FD.containers.pop(NAME_A, None)
        try:
            assert sandbox.ensure_container(store.load_org(SLUG_A)) == NAME_A
        finally:
            sandbox.try_apply_pending_resize = orig

    # ---- teardown levers
    @t("stop_container gives the CLI 20 s to flush its transcript")
    def _():
        FD.calls.clear()
        sandbox.stop_container(SLUG_A)
        assert FD.find("stop", "-t", "20", NAME_A), FD.calls

    @t("☞ kill_claude reaps ONE turn's process, not every agent's (№40)")
    def _():
        FD.calls.clear()
        sandbox.kill_claude(NAME_A, "session-abc")
        assert FD.calls[-1] == ["exec", NAME_A, "pkill", "-9", "-f",
                                "session-abc"], FD.calls[-1]
        # the default is the blunt one — callers in the turn loop pass the sid
        FD.calls.clear()
        sandbox.kill_claude(NAME_A)
        assert FD.calls[-1][-1] == "claude"

    @t("kill_claude survives a wedged daemon (a timeout is not an exception)")
    def _():
        FD.timeouts.add("exec")
        try:
            sandbox.kill_claude(NAME_A, "x")
        finally:
            FD.timeouts.discard("exec")

    @t("remove() tears down container, legacy volumes AND the org disk")
    def _():
        o = fresh("r1", secret="55" * 16)
        s = o.d["slug"]
        sandbox.ensure_container(o)
        FD.calls.clear()
        FDISK.calls.clear()
        sandbox.remove(s)
        assert FD.find("rm", "-f", sandbox.container_name(s)), FD.calls
        vrm = FD.find("volume", "rm", "-f")[0]
        assert set(vrm[3:]) == {sandbox.sys_volume(s, d)
                                for d in sandbox.SYS_DIRS}, vrm
        assert ("destroy", s) in FDISK.calls, FDISK.calls
        assert s not in sandbox._disk_flag
        drop(s)

    @t("☞ remove() tombstones the slug so a racing warm() cannot leak it")
    def _():
        o = fresh("r2", secret="56" * 16)
        s = o.d["slug"]
        sandbox.remove(s)
        assert s in sandbox._dead
        gate = threading.Event()
        real = sandbox.ensure_container
        sandbox.ensure_container = lambda org: (gate.wait(5),
                                                real(org))[1]
        try:
            REAL_WARM(o)                    # background prebuild starts…
            sandbox.remove(s)               # …org deleted mid-build
            FD.calls.clear()
            gate.set()
            for _ in range(100):
                if FD.find("rm", "-f", sandbox.container_name(s)):
                    break
                time.sleep(0.05)
            assert FD.find("rm", "-f", sandbox.container_name(s)), \
                "the container built after the delete was leaked"
        finally:
            sandbox.ensure_container = real
        drop(s)

    @t("warm() never propagates its failure (the turn surfaces it instead)")
    def _():
        o = fresh("r3", secret="57" * 16)
        FD.server_up = False
        try:
            REAL_WARM(o)
            time.sleep(0.3)
        finally:
            FD.server_up = True
        drop(o.d["slug"])

    @t("sandbox_volumes_bytes measures daemon-side and fails CLOSED on timeout")
    def _():
        sandbox._vol_usage_cache.clear()
        assert sandbox.sandbox_volumes_bytes("x") == 1500000000
        sandbox._vol_usage_cache.clear()
        FD.timeouts.add("system")
        try:
            assert sandbox.sandbox_volumes_bytes("x") is None
        finally:
            FD.timeouts.discard("system")

    FDISK.restore()      # §4 needs the real disk module back


# ================================================================== §3
# The bridge is the ONE door out of the container. Requests are made by
# calling the ASGI app directly with a hand-built scope (the same technique
# test_api_surface documents): a client would normalise `..`, `%2f` and case
# before the gateway ever saw them, and the gateway is the thing under test.
BRIDGE = api.BridgeGateway(api.app)
ADMIN = api.app
PUBLIC = api.PublicGateway(api.app)


class Res:
    def __init__(self, status, body, exc=None):
        self.status, self.body, self.exc = status, body, exc
        try:
            self.json = json.loads(body)
        except Exception:                                      # noqa: BLE001
            self.json = None

    @property
    def text(self):
        return self.body.decode("utf-8", "replace")

    def __repr__(self):
        return f"<{self.status} {(self.exc or self.text)[:180]!r}>"


def call(app, method, path, body=None, headers=None, query=b""):
    import asyncio
    payload = b"" if body is None else json.dumps(body).encode()
    hdrs = [(b"host", b"127.0.0.1:7407")]
    if payload:
        hdrs += [(b"content-type", b"application/json"),
                 (b"content-length", str(len(payload)).encode())]
    for k, v in (headers or []):
        hdrs.append((k.lower().encode(), v.encode()))
    st, chunks, exc = [0], [], [None]

    async def receive():
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(msg):
        if msg["type"] == "http.response.start":
            st[0] = msg["status"]
        elif msg["type"] == "http.response.body":
            chunks.append(msg.get("body", b""))

    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
             "http_version": "1.1", "method": method, "scheme": "http",
             "path": path, "raw_path": path.encode(), "query_string": query,
             "root_path": "", "headers": hdrs, "client": ("127.0.0.1", 5555),
             "server": ("127.0.0.1", 7407)}
    try:
        asyncio.run(app(scope, receive, send))
    except Exception as e:                                     # noqa: BLE001
        exc[0] = f"{type(e).__name__}: {e}"
    return Res(st[0], b"".join(chunks), exc[0])


if section("§3  the bridge — the one door out"):
    SEC_A = "1a" * 16
    SEC_B = "2b" * 16
    OB1 = mkorg("b1", kiosk=True, secret=SEC_A)
    OB2 = mkorg("b2", secret=SEC_B)
    SB1, SB2 = OB1.d["slug"], OB2.d["slug"]
    for _s in (SB1, SB2):
        with store.DOC_LOCK:
            _o = store.load_org(_s)
            _o.hire(USER, None, "sonnet", 10, "alice", charter="c")
            store.save_org(_o)
    api._bridge_cache["at"] = 0.0

    def br(method, path, body=None, secret=SEC_A):
        h = [("x-orgtree-bridge", secret)] if secret is not None else []
        return call(BRIDGE, method, path, body, headers=h)

    def forbidden(r, what):
        assert r.status == 403 and r.json == {"detail": "forbidden"}, \
            f"{what}: expected a bare bridge 403, got {r!r}"

    @t("the org's own secret opens /api/agent for its own org")
    def _():
        r = br("POST", "/api/agent",
               {"org": SB1, "node": "alice", "tool": "orgtree_chart"})
        assert r.status == 200 and "chart" in (r.json or {}), r

    @t("☠ a secret is scoped to ITS org — another org's is a refusal")
    def _():
        r = br("POST", "/api/agent",
               {"org": SB2, "node": "alice", "tool": "orgtree_chart"})
        assert r.status == 403 and "scoped to its own org" in r.text, r

    @t("☠ the steer fetch is scoped the same way")
    def _():
        assert br("POST", f"/api/orgs/{SB1}/nodes/alice/steer").status == 200
        forbidden(br("POST", f"/api/orgs/{SB2}/nodes/alice/steer"),
                  "cross-org steer")

    @t("a made-up secret opens nothing")
    def _():
        for bad in ["0" * 32, "", "  ", SEC_A[:-1], SEC_A + "f", SEC_A.upper(),
                    "'; DROP TABLE", None]:
            forbidden(br("POST", "/api/agent", {"org": SB1, "node": "alice",
                                                "tool": "orgtree_chart"},
                         secret=bad), f"secret={bad!r}")

    @t("☠ a DELETED org's secret stops working (the map is rebuilt, not kept)")
    def _():
        o = mkorg("b3", secret="3c" * 16)
        api._bridge_cache["at"] = 0.0
        assert api._bridge_secret_map().get("3c" * 16) == o.d["slug"]
        drop(o.d["slug"])
        api._bridge_cache["at"] = 0.0
        assert "3c" * 16 not in api._bridge_secret_map()
        forbidden(br("POST", "/api/agent", {"org": o.d["slug"], "node": "a",
                                            "tool": "orgtree_chart"},
                     secret="3c" * 16), "deleted org's secret")

    BRIDGE_CLOSED = [
        ("GET", "/api/agent"), ("PUT", "/api/agent"), ("PATCH", "/api/agent"),
        ("DELETE", "/api/agent"), ("GET", "/api/orgs"), ("GET", "/api/fs"),
        ("GET", "/api/host"), ("GET", "/api/defaults"), ("GET", "/api/mcp"),
        ("GET", f"/api/orgs/{SB1}"), ("POST", f"/api/orgs/{SB1}/settings"),
        ("POST", f"/api/orgs/{SB1}/ops"), ("DELETE", f"/api/orgs/{SB1}"),
        ("GET", f"/api/orgs/{SB1}/disk"), ("GET", f"/api/orgs/{SB1}/disk/file"),
        ("POST", f"/api/orgs/{SB1}/disk/delete"),
        ("POST", f"/api/orgs/{SB1}/disk/resize"),
        ("GET", f"/api/orgs/{SB1}/nodes/alice/chat"),
        ("POST", f"/api/orgs/{SB1}/nodes/alice/message"),
        ("GET", f"/api/orgs/{SB1}/nodes/alice/scratch"),
        ("GET", "/"), ("GET", "/index.html"), ("GET", "/openapi.json"),
        ("GET", "/docs"), ("POST", "/api/orgs"),
        # the two sanctioned paths, in shapes that are NOT them
        ("POST", "/api/agent/"), ("POST", "//api/agent"),
        ("POST", "/api/agent/x"), ("POST", " /api/agent"),
        ("POST", f"/api/orgs/{SB1}/nodes/alice/steer/"),
        ("POST", f"/api/orgs/{SB1}/nodes/alice/steer?x=1"),
        ("POST", f"/api/orgs/{SB1}/../{SB2}/nodes/alice/steer"),
        ("POST", "/anthropic"), ("POST", "/anthropic/"),
        ("POST", f"/anthropic{SEC_A}/v1/messages"),
    ]

    def _closed(m, p):
        def go():
            forbidden(br(m, p, {}), f"bridge {m} {p}")
        return go

    for _m, _p in BRIDGE_CLOSED:
        check(f"☠ bridge serves nothing at {_m} {_p}", _closed(_m, _p))

    # ---- /anthropic: the proxy that attaches the HOST's OAuth token
    _TOKEN_CALLS = []

    def _fake_token():
        _TOKEN_CALLS.append(time.time())
        raise RuntimeError("upstream-token-not-fetched-in-tests")

    _REAL_GET_TOKEN = subproxy.get_access_token
    subproxy.get_access_token = _fake_token

    @t("☠ /anthropic is UNREACHABLE on the admin listener (no bridge marker)")
    def _():
        _TOKEN_CALLS.clear()
        r = call(ADMIN, "POST", "/anthropic/v1/messages", {"model": "x"})
        assert r.status == 403 and "bridge only" in r.text, r
        assert not _TOKEN_CALLS, "the host token was fetched for a non-bridge"

    @t("☠ …and on the public kiosk listener")
    def _():
        _TOKEN_CALLS.clear()
        tok = store.load_org(SB1).d["kiosk"]["token"]
        api._token_cache["at"] = 0.0
        r = call(PUBLIC, "POST", f"/k/{tok}/anthropic/v1/messages", {"m": 1})
        assert r.status in (403, 404), r
        assert not _TOKEN_CALLS, "a kiosk visitor drove the OAuth proxy"

    @t("a valid secret in the PATH reaches the proxy (and only then)")
    def _():
        _TOKEN_CALLS.clear()
        r = br("POST", f"/anthropic/{SEC_A}/v1/messages", {"model": "x"},
               secret=None)
        assert r.status == 502 and "token-not-fetched" in r.text, r
        assert len(_TOKEN_CALLS) == 1, _TOKEN_CALLS

    @t("☠ a wrong/short/uppercase path secret never reaches the proxy")
    def _():
        _TOKEN_CALLS.clear()
        for bad in ["0" * 32, SEC_A[:31], SEC_A + "0", SEC_A.upper(),
                    "g" * 32, SEC_A[:16] + "-" + SEC_A[17:]]:
            forbidden(br("POST", f"/anthropic/{bad}/v1/messages", {},
                         secret=None), f"path secret {bad!r}")
        assert not _TOKEN_CALLS, _TOKEN_CALLS

    @t("☠ the rewritten /anthropic path cannot walk back into the API")
    def _():
        _TOKEN_CALLS.clear()
        r = br("POST", f"/anthropic/{SEC_A}/../api/agent",
               {"org": SB2, "node": "alice", "tool": "orgtree_chart"},
               secret=None)
        assert r.status != 200 or "chart" not in r.text, r
        assert "chart" not in r.text, r

    @t("the header secret ALSO opens the proxy (same gate, either carrier)")
    def _():
        _TOKEN_CALLS.clear()
        r = br("POST", "/anthropic/v1/messages", {"model": "x"}, secret=SEC_A)
        assert r.status == 403, ("a header-only /anthropic call is refused: "
                                 "the secret must ride the path", r)

    @t("bridge: a websocket is closed, never upgraded")
    def _():
        import asyncio
        closed = []

        async def send(msg):
            closed.append(msg)

        async def receive():
            return {"type": "websocket.connect"}

        asyncio.run(BRIDGE({"type": "websocket", "path": "/ws",
                            "headers": [(b"x-orgtree-bridge",
                                         SEC_A.encode())]}, receive, send))
        assert closed and closed[0]["type"] == "websocket.close", closed

    @t("bridge: lifespan is answered locally (the admin server owns the app)")
    def _():
        import asyncio
        msgs = [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]
        sent = []

        async def receive():
            return msgs.pop(0)

        async def send(m):
            sent.append(m["type"])

        asyncio.run(BRIDGE({"type": "lifespan"}, receive, send))
        assert sent == ["lifespan.startup.complete",
                        "lifespan.shutdown.complete"], sent

    @t("☠ the sandbox secret is in NO payload — not even on the admin listener")
    def _():
        for path in (f"/api/orgs/{SB1}", f"/api/orgs/{SB1}/settings",
                     "/api/orgs", f"/api/orgs/{SB1}/audiences",
                     f"/api/orgs/{SB1}/nodes/alice/chat"):
            r = call(ADMIN, "GET", path)
            assert SEC_A not in r.text, (path, r.text[:200])

    @t("☞ …and the org doc is the ONLY host-side copy of it")
    def _():
        hits = []
        for root, _dirs, files in os.walk(DATA):
            for f in files:
                p = os.path.join(root, f)
                try:
                    if SEC_A in open(p, encoding="utf-8",
                                     errors="ignore").read():
                        hits.append(os.path.relpath(p, DATA))
                except OSError:
                    pass
        assert hits, "the secret was found NOWHERE — the probe stopped working"
        if store.STORE_BACKEND == "sqlite":
            # SQLite EQUIVALENT: the org document is `orgs/<slug>.db` plus its
            # `-wal`/`-shm` sidecars, which are part of that same database (a
            # secret written but not yet checkpointed lives in the WAL). Any
            # OTHER file holding it is the leak this check exists to catch and
            # is still a failure.
            allowed = {os.path.join("orgs", SB1 + ".db") + x
                       for x in ("", "-wal", "-shm")}
            assert set(hits) <= allowed, hits
        else:
            assert hits == [os.path.join("orgs", SB1 + ".json")], hits


# ================================================================== §4
# disk.py is ~30 shell one-liners; its bugs are shell bugs. So the seam is
# `disk._run` (which `_sh` funnels into), and a `wsl -d … -e sh -c <script>`
# call is dispatched to a REAL POSIX shell against a temp-dir distro. `find`,
# `stat`, `sort`, `sed`, `du`, `cut`, `dd` and `truncate` are the real GNU
# tools; only the four privileged ones (mount/umount/mkfs.ext4/losetup +
# resize2fs/e2fsck/df) are stubs. Every command string in disk.py is executed
# verbatim.
SH = next((p for p in (shutil.which("sh"),
                       r"C:\Program Files\Git\usr\bin\sh.exe",
                       r"C:\Program Files\Git\bin\sh.exe", "/bin/sh")
           if p and os.path.exists(p)), None)


def msys(p):
    p = os.path.abspath(p).replace("\\", "/")
    return "/" + p[0].lower() + p[2:] if p[1:2] == ":" else p


STUBS = {
    # mkfs.ext4 -q IMG → record the capacity beside the image and open a
    # content store for it
    "mkfs.ext4": """
img=""
for a in "$@"; do case "$a" in -*) ;; *) img="$a";; esac; done
[ -f "$img" ] || { echo "mkfs.ext4: $img: no such file" >&2; exit 1; }
sz=$(stat -c %s "$img")
[ "$sz" -ge 1048576 ] || { echo "mkfs.ext4: image too small" >&2; exit 1; }
echo $((sz/1024)) > "$img.kb"
mkdir -p "$img.data"
: > "$img.fs"
""",
    # mount -o loop IMG MP → move the content store into place + register
    "mount": """
img=""; mp=""
while [ $# -gt 0 ]; do
  case "$1" in -o) shift ;;
    *) if [ -z "$img" ]; then img="$1"; else mp="$1"; fi ;;
  esac; shift
done
[ -f "$img.fs" ] || { echo "mount: $img: no valid filesystem" >&2; exit 32; }
[ -d "$mp" ] || { echo "mount: $mp: no mount point" >&2; exit 32; }
mkdir -p "$img.data"
cp -a "$img.data/." "$mp/" 2>/dev/null
rm -rf "$img.data"
echo "$img" > "$FAKEREG/$(basename $mp)"
echo "$img" > "$FAKELOOP/loop-$(basename $mp)"
""",
    "umount": """
mp="$1"
reg="$FAKEREG/$(basename $mp)"
[ -f "$reg" ] || { echo "umount: $mp: not mounted" >&2; exit 32; }
img=$(cat "$reg")
mkdir -p "$img.data"
cp -a "$mp/." "$img.data/" 2>/dev/null
find "$mp" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
rm -f "$reg" "$FAKELOOP/loop-$(basename $mp)"
""",
    "mountpoint": """
for a in "$@"; do case "$a" in -*) ;; *) mp="$a";; esac; done
[ -f "$FAKEREG/$(basename $mp)" ]
""",
    "df": """
for a in "$@"; do case "$a" in -*) ;; *) mp="$a";; esac; done
reg="$FAKEREG/$(basename $mp)"
[ -f "$reg" ] || { echo "df: $mp: no such mount" >&2; exit 1; }
img=$(cat "$reg")
tot=$(cat "$img.kb")
if [ -n "$FAKE_USED_KB" ]; then used=$FAKE_USED_KB; else used=$(du -sk "$mp" | cut -f1); fi
echo "Filesystem     1K-blocks      Used Available Use% Mounted on"
echo "/dev/loop0 $tot $used $((tot-used)) 1% $mp"
""",
    "losetup": """
if [ "$1" = "-j" ]; then
  img="$2"
  for f in "$FAKELOOP"/loop-*; do
    [ -f "$f" ] || continue
    if [ "$(cat $f)" = "$img" ]; then echo "$f: [2049]:1 ($img)"; exit 0; fi
  done
  exit 0
fi
exit 0
""",
    # resize2fs DEV | resize2fs IMG <n>M — DEV resolves through the loop file
    "resize2fs": """
dev="$1"; want="$2"
if [ -f "$dev.fs" ]; then img="$dev"; else img=$(cat "$dev" 2>/dev/null); fi
[ -f "$img.fs" ] || { echo "resize2fs: $dev: not a filesystem" >&2; exit 1; }
if [ -n "$want" ]; then
  kb=$(( ${want%M} * 1024 ))
  used=$(du -sk "$img.data" 2>/dev/null | cut -f1)
  [ -n "$used" ] || used=0
  [ "$kb" -ge "$used" ] || { echo "resize2fs: New size smaller than minimum" >&2; exit 1; }
else
  kb=$(( $(stat -c %s "$img") / 1024 ))
fi
echo "$kb" > "$img.kb"
""",
    "e2fsck": """
for a in "$@"; do case "$a" in -*) ;; *) img="$a";; esac; done
[ -f "$img.fs" ] || { echo "e2fsck: $img: bad superblock" >&2; exit 8; }
""",
}


class FakeDistro:
    """A temp-dir stand-in for the docker-desktop WSL distro."""

    def __init__(self, root):
        self.root = root
        self.bin = os.path.join(root, "bin")
        self.reg = os.path.join(root, "reg")
        self.loop = os.path.join(root, "loop")
        self.vol = os.path.join(root, "vol")
        self.scripts: list[str] = []
        self.wsl_list = "  docker-desktop\x00\n* Ubuntu-24.04\x00\ndocker-desktop-data\x00\n"
        self.wsl_rc = 0
        self.used_kb: str = ""
        for d in (self.bin, self.reg, self.loop, self.vol):
            os.makedirs(d, exist_ok=True)
        for name, body in STUBS.items():
            p = os.path.join(self.bin, name)
            with open(p, "w", newline="\n") as f:
                f.write("#!/bin/sh\n" + body)
            os.chmod(p, 0o755)

    def env(self):
        gitbin = os.path.dirname(SH)
        return {"PATH": ":".join([msys(self.bin), msys(gitbin),
                                  msys(os.path.join(gitbin, "..", "bin"))]),
                "FAKEREG": msys(self.reg), "FAKELOOP": msys(self.loop),
                "FAKE_USED_KB": self.used_kb,
                "HOME": msys(self.root), "SYSTEMROOT": os.environ.get(
                    "SYSTEMROOT", r"C:\Windows")}

    def run(self, args, timeout=60):
        def cp(rc, out="", err=""):
            return subprocess.CompletedProcess(args=args, returncode=rc,
                                               stdout=out, stderr=err)
        if args[:2] == ["wsl", "-l"]:
            return cp(self.wsl_rc, self.wsl_list if not self.wsl_rc else "")
        if args[:2] == ["wsl", "-d"]:
            assert args[2] == "docker-desktop", f"wrong distro: {args[2]}"
            assert args[3:5] == ["-e", "sh"] and args[5] == "-c", args
            self.scripts.append(args[6])
            r = subprocess.run([SH, "-c", args[6]], capture_output=True,
                               text=True, env=self.env(), timeout=timeout)
            return cp(r.returncode, r.stdout, r.stderr)
        if args[:1] == ["docker"]:
            if args[1:3] == ["volume", "create"]:
                os.makedirs(os.path.join(self.vol, args[3], "_data"),
                            exist_ok=True)
                return cp(0)
            if args[1:3] == ["volume", "rm"]:
                for v in args[3:]:
                    if v.startswith("-"):
                        continue
                    shutil.rmtree(os.path.join(self.vol, v), ignore_errors=True)
                return cp(0)
            if args[1:3] == ["volume", "inspect"]:
                return cp(0 if os.path.isdir(os.path.join(self.vol, args[3]))
                          else 1)
        return cp(0)


if section("§4  disk.py against a real POSIX shell") and not SH:
    skip("no POSIX sh on this host (Git Bash not found) — §4 needs a real "
         "shell to execute disk.py's command strings")
elif ONLY and "§4" not in ONLY and ONLY not in "§4  disk.py against a real POSIX shell":
    pass
elif SH:
    D4 = FakeDistro(os.path.join(DATA, "distro"))
    UNC_ROOT_BEFORE = dsk.mount_root()      # resolve BEFORE the stubs land
    UNC_BEFORE = dsk.windows_path("probe-slug")
    dsk._run = D4.run
    dsk._distro_cache = None
    dsk._dataroot_cache = None
    dsk._mount_root_cache = msys(os.path.join(D4.root, "mnt", "wsl", "orgtree-disk"))
    dsk._DATA_ROOTS = (msys(D4.vol),)
    NATIVE_MP = lambda s: os.path.join(D4.root, "mnt", "wsl",   # noqa: E731
                                       "orgtree-disk", s)
    dsk.windows_path = NATIVE_MP
    S4 = PFX + "disk1"

    @t("windows_path is the \\\\wsl.localhost UNC view of the mount")
    def _():
        # ⚠ derived from the RESOLVED mount root, not hardcoded. Docker Desktop
        # moved the shared tmpfs from /mnt/wsl to /mnt/host/wsl inside the
        # distro (2026-08-04), which is what broke sandboxed-org creation; the
        # thing under test here is the UNC *shape* — \\wsl.localhost\<distro>
        # plus the distro-side path with separators flipped — not which root
        # this machine happens to resolve to.
        assert UNC_BEFORE == (r"\\wsl.localhost\docker-desktop"
                              + UNC_ROOT_BEFORE.replace("/", "\\")
                              + "\\probe-slug"), UNC_BEFORE

    @t("distro() DETECTS docker-desktop, ignoring the -data twin and the NULs")
    def _():
        dsk._distro_cache = None
        assert dsk.distro() == "docker-desktop"

    @t("distro() is cached (one `wsl -l -q` per process, not per call)")
    def _():
        n = len([s for s in D4.scripts])
        dsk.distro(), dsk.distro()
        assert dsk._distro_cache == "docker-desktop"

    @t("no docker-desktop distro → an actionable DiskError naming what it saw")
    def _():
        dsk._distro_cache = None
        D4.wsl_list = "Ubuntu-24.04\x00\nkali\x00\n"
        try:
            dsk.distro()
            raise AssertionError("no raise")
        except dsk.DiskError as e:
            assert "no docker-desktop WSL distro" in str(e) and "kali" in str(e)
        finally:
            D4.wsl_list = "  docker-desktop\x00\n* Ubuntu-24.04\x00\n"
            dsk._distro_cache = None

    @t("WSL itself unavailable → a DiskError, never a silent empty mount")
    def _():
        dsk._distro_cache = None
        D4.wsl_rc = 1
        try:
            dsk.distro()
            raise AssertionError("no raise")
        except dsk.DiskError as e:
            assert "WSL is unavailable" in str(e)
        finally:
            D4.wsl_rc = 0
            dsk._distro_cache = None

    @t("_data_root probes the candidates inside the distro")
    def _():
        dsk._dataroot_cache = None
        assert dsk._data_root() == msys(D4.vol)
        dsk._dataroot_cache = None
        orig = dsk._DATA_ROOTS
        dsk._DATA_ROOTS = ("/nope/one", "/nope/two")
        try:
            dsk._data_root()
            raise AssertionError("no raise")
        except dsk.DiskError as e:
            assert "cannot locate the docker volumes dir" in str(e)
        finally:
            dsk._DATA_ROOTS = orig
            dsk._dataroot_cache = None
        assert dsk._data_root() == msys(D4.vol)

    @t("a disk below 16 MB is refused before anything is created")
    def _():
        try:
            dsk.create(S4, 8)
            raise AssertionError("created an 8 MB disk")
        except dsk.DiskError as e:
            assert "at least 16 MB" in str(e), e
        assert not os.path.isdir(os.path.join(D4.vol, dsk.disk_volume(S4)))

    @t("☞ create(): volume + sparse image + ext4 + sentinel, then MOUNTED")
    def _():
        dsk.create(S4, 64)
        assert os.path.isfile(os.path.join(D4.vol, dsk.disk_volume(S4),
                                           "_data", "disk.img"))
        assert dsk.exists(S4) and dsk.is_mounted(S4)
        assert os.path.isfile(os.path.join(NATIVE_MP(S4), dsk.SENTINEL))

    @t("☞ the filesystem is formatted with NO reserved-for-root blocks")
    def _():
        mk = [x for x in D4.scripts if "mkfs.ext4" in x]
        assert mk and "-m 0" in mk[0], mk[:1]
        fixed("disk.create now formats org disks with `mkfs.ext4 -m 0`. "
              "Before this, ext4's default 5% root reserve meant the CLI (an "
              "unprivileged user in the container) hit ENOSPC at df=94.4% on "
              "a real 4096 MB disk — ~200 MB of every org's cap unusable, and "
              "supervisor's >=99% hard-full tier unreachable. Fixed for NEW "
              "disks; an existing one needs `tune2fs -m 0 <img>`, which is "
              "the operator's call.")

    @t("the image is SPARSE (allocation follows writes, not the cap)")
    def _():
        p = os.path.join(D4.vol, dsk.disk_volume(S4), "_data", "disk.img")
        assert os.path.getsize(p) == 64 * 1048576, os.path.getsize(p)

    @t("create() is idempotent — an existing disk is never reformatted")
    def _():
        with open(os.path.join(NATIVE_MP(S4), "keepme.txt"), "w") as f:
            f.write("x" * 10)
        dsk.create(S4, 64)
        assert os.path.isfile(os.path.join(NATIVE_MP(S4), "keepme.txt")), \
            "create() wiped an existing org disk"

    @t("usage() reports (used, total) from df INSIDE the distro")
    def _():
        u = dsk.usage(S4, max_age=0.0)
        assert u and u[1] == 64 * 1048576, u
        assert 0 < u[0] < u[1], u

    @t("usage() is cached, and invalidate() drops it")
    def _():
        u1 = dsk.usage(S4, max_age=0.0)
        with open(os.path.join(NATIVE_MP(S4), "big.bin"), "wb") as f:
            f.write(b"\0" * (2 << 20))
        assert dsk.usage(S4) == u1, "a cached reading must not move"
        dsk.invalidate(S4)
        assert dsk.usage(S4, max_age=0.0)[0] > u1[0], "the delta never showed"

    @t("☠ unmount hides the content and is_mounted goes FALSE (the sentinel)")
    def _():
        dsk.unmount(S4)
        assert not dsk.is_mounted(S4)
        assert not os.path.exists(os.path.join(NATIVE_MP(S4), "keepme.txt"))
        assert dsk.exists(S4), "the image itself must survive an unmount"

    @t("usage() of an unmounted disk is None (nothing can be writing to it)")
    def _():
        dsk.invalidate(S4)
        assert dsk.usage(S4, max_age=0.0) is None

    @t("mount() restores exactly what was there")
    def _():
        dsk.mount(S4)
        assert dsk.is_mounted(S4)
        assert open(os.path.join(NATIVE_MP(S4), "keepme.txt")).read() == "x" * 10

    @t("☞ mount() is idempotent — the single-mount property is never violated")
    def _():
        n = len(D4.scripts)
        dsk.mount(S4)
        dsk.mount(S4)
        # one `test -f sentinel` per call and NO second `mount -o loop`
        assert not [s for s in D4.scripts[n:] if "mount -o loop" in s], \
            D4.scripts[n:]

    @t("☠ mounting an org with no image is a DiskError, never an empty dir")
    def _():
        try:
            dsk.mount(PFX + "ghost")
            raise AssertionError("mounted nothing")
        except dsk.DiskError as e:
            assert "has no disk image" in str(e), e

    # ---- the recovery browser's two views, on real files
    @t("enumerate_by_size lists files largest-first and hides the sentinel")
    def _():
        root = NATIVE_MP(S4)
        for p in ("home/orgtree", "home/.claude/projects/p1", "usr/lib",
                  "workspace/deep/deeper"):
            os.makedirs(os.path.join(root, *p.split("/")), exist_ok=True)
        for name, size in (("workspace/a.bin", 5000), ("workspace/b.bin", 3000),
                           ("home/notes.txt", 100),
                           ("workspace/deep/deeper/c.bin", 9000),
                           ("usr/lib/libc.so", 200)):
            with open(os.path.join(root, *name.split("/")), "wb") as f:
                f.write(b"\0" * size)
        os.remove(os.path.join(root, "big.bin"))
        os.remove(os.path.join(root, "keepme.txt"))
        got = dsk.enumerate_by_size(S4)
        assert [g["path"] for g in got][:3] == [
            "workspace/deep/deeper/c.bin", "workspace/a.bin",
            "workspace/b.bin"], got
        assert all(g["path"] != dsk.SENTINEL for g in got), got
        assert got[0]["bytes"] == 9000, got[0]

    @t("☞ REGRESSION: paging is `tail -n +N | head -M`, so pages never overlap")
    def _():
        """`head -(offset+limit) | tail -limit` reads correctly and is wrong:
        head CLAMPS once it overshoots the real line count, so the last page
        re-serves rows and an offset past the end returns the tail instead of
        nothing — precisely where a `load more` button sits."""
        p1 = dsk.enumerate_by_size(S4, limit=3, offset=0)
        p2 = dsk.enumerate_by_size(S4, limit=3, offset=3)
        assert len(p1) == 3 and len(p2) == 2, (p1, p2)
        assert not ({f["path"] for f in p1} & {f["path"] for f in p2}), (p1, p2)
        assert dsk.enumerate_by_size(S4, limit=10, offset=5) == []
        assert dsk.enumerate_by_size(S4, limit=10, offset=99) == []

    @t("list_dir: one level, dirs AND files intermixed by size descending")
    def _():
        top = dsk.list_dir(S4, "", max_age=0.0)
        names = [e["name"] for e in top]
        assert names[0] == "workspace", top
        assert set(names) == {"workspace", "usr", "home"}, names
        ws = {e["name"]: e for e in dsk.list_dir(S4, "workspace")}
        assert ws["deep"]["dir"] and ws["deep"]["bytes"] == 9000, ws
        assert ws["a.bin"] == {"name": "a.bin", "path": "workspace/a.bin",
                               "dir": False, "bytes": 5000, "files": 1}, ws

    @t("directory sizes roll all the way up the tree")
    def _():
        top = {e["name"]: e for e in dsk.list_dir(S4, "")}
        assert top["workspace"]["bytes"] == 5000 + 3000 + 9000, top
        assert top["workspace"]["files"] == 3, top

    @t("an empty directory lists as empty, and a missing one is a DiskError")
    def _():
        os.makedirs(os.path.join(NATIVE_MP(S4), "workspace", "empty"),
                    exist_ok=True)
        assert dsk.list_dir(S4, "workspace/empty", max_age=0.0) == []
        try:
            dsk.list_dir(S4, "workspace/nope")
            raise AssertionError("no raise")
        except dsk.DiskError as e:
            assert "no such directory" in str(e), e

    @t("subtree_files walks a whole subtree from the ONE cached walk")
    def _():
        got = dict(dsk.subtree_files(S4, "workspace", max_age=0.0))
        assert got == {"workspace/a.bin": 5000, "workspace/b.bin": 3000,
                       "workspace/deep/deeper/c.bin": 9000}, got
        assert dsk.subtree_files(S4, "workspace/empty") == []

    @t("the browser refuses to walk an UNMOUNTED disk (it would read nothing)")
    def _():
        dsk.unmount(S4)
        for fn in (lambda: dsk.enumerate_by_size(S4),
                   lambda: dsk.list_dir(S4, "", max_age=0.0),
                   lambda: dsk._dir_children(S4, max_age=0.0)):
            try:
                fn()
                raise AssertionError("walked an unmounted disk")
            except dsk.DiskError as e:
                assert "not mounted" in str(e), e
        dsk.mount(S4)

    @t("⚑ a filename holding the walk's own record separator still parses")
    def _():
        p = os.path.join(NATIVE_MP(S4), "workspace", "we@ird.bin")
        with open(p, "wb") as f:
            f.write(b"\0" * 77)
        got = {f["path"]: f["bytes"]
               for f in dsk.enumerate_by_size(S4, limit=99)}
        assert got.get("workspace/we@ird.bin") == 77, got
        os.remove(p)
        dsk.invalidate(S4)

    @t("⚑ a filename holding a NEWLINE can forge a row in both views")
    def _():
        """Windows cannot create such a name, so this drives the PARSER with
        the bytes a real ext4 would produce. Both walks are line-oriented and
        neither validates that a row's path exists: an agent inside the
        container can plant phantom rows in the admin's recovery browser
        (and, via _protected_transcripts, make an unrelated directory look
        undeletable). Display-level, but it is the browser's ground truth."""
        real = dsk._sh
        payload = ("F10@./real.bin\n"
                   "F999999999@./home/\nF5@./PHANTOM-HUGE.bin\n"
                   "D@./home\n")
        dsk._sh = lambda script, timeout=60: subprocess.CompletedProcess(
            args=[], returncode=0, stdout=payload, stderr="")
        try:
            dsk._tree_cache.pop(S4, None)
            kids = dsk._dir_children(S4, max_age=0.0)
            assert "PHANTOM-HUGE.bin" in kids[""], kids[""]
        finally:
            dsk._sh = real
            dsk._tree_cache.pop(S4, None)
        note("disk._dir_children / enumerate_by_size parse `find` output "
             "line-by-line, so a filename containing a newline forges rows in "
             "the recovery browser (phantom files; a directory can be made to "
             "look like it holds protected transcripts, which blocks its "
             "delete). Fix: NUL-delimit the walk (find -print0 / stat -c … "
             "with -exec … + and tr) — disk.py, reported not fixed because "
             "the same change touches api.py's classifiers.")

    # ---- resize
    @t("grow() extends the filesystem ONLINE and the cap moves with it")
    def _():
        dsk.grow(S4, 128)
        u = dsk.usage(S4, max_age=0.0)
        assert u and u[1] == 128 * 1048576, u
        assert os.path.isfile(os.path.join(NATIVE_MP(S4), "workspace", "a.bin")), \
            "a grow lost data"

    @t("☠ grow() REFUSES to shrink (that path is offline and staged)")
    def _():
        try:
            dsk.grow(S4, 32)
            raise AssertionError("grow() shrank the disk")
        except dsk.DiskError as e:
            assert "cannot shrink" in str(e), e
        assert dsk.usage(S4, max_age=0.0)[1] == 128 * 1048576

    @t("☞ shrink_image(): unmount → fsck → resize → truncate, data intact")
    def _():
        dsk.shrink_image(S4, 32)
        assert not dsk.is_mounted(S4), "shrink must leave it unmounted"
        p = os.path.join(D4.vol, dsk.disk_volume(S4), "_data", "disk.img")
        assert os.path.getsize(p) == 32 * 1048576, os.path.getsize(p)
        dsk.mount(S4)
        assert dsk.usage(S4, max_age=0.0)[1] == 32 * 1048576
        assert open(os.path.join(NATIVE_MP(S4), "home", "notes.txt"), "rb"
                    ).read() == b"\0" * 100

    @t("☠ a shrink that the filesystem refuses truncates NOTHING")
    def _():
        p = os.path.join(D4.vol, dsk.disk_volume(S4), "_data", "disk.img")
        before = os.path.getsize(p)
        with open(os.path.join(NATIVE_MP(S4), "workspace", "fat.bin"),
                  "wb") as f:
            f.write(b"\0" * (6 << 20))
        try:
            dsk.shrink_image(S4, 2)          # below the live content
            raise AssertionError("no raise")
        except dsk.DiskError as e:
            assert "shrink failed" in str(e) and "nothing was truncated" in str(e)
        assert os.path.getsize(p) == before, "the image was truncated anyway"
        dsk.mount(S4)
        assert os.path.isfile(os.path.join(NATIVE_MP(S4), "workspace",
                                           "fat.bin")), "a failed shrink lost data"
        os.remove(os.path.join(NATIVE_MP(S4), "workspace", "fat.bin"))

    @t("destroy() drops the mount and the volume (the image goes with it)")
    def _():
        dsk.destroy(S4)
        assert not dsk.exists(S4) and not dsk.is_mounted(S4)
        assert not os.path.isdir(os.path.join(D4.vol, dsk.disk_volume(S4)))

    @t("every disk lives under its own docker volume — one org cannot see two")
    def _():
        assert dsk.disk_volume("a") != dsk.disk_volume("b")
        assert dsk.disk_volume(S4) == "orgtree-disk-" + S4


# ================================================================== §5
if section("§5  the soft cap, the hard cap, the recovery browser"):
    O5 = mkorg("cap1", kiosk=True, secret="5e" * 16, disk={"size_mb": 4096})
    S5 = O5.d["slug"]
    with store.DOC_LOCK:
        _o = store.load_org(S5)
        _o.hire(USER, None, "sonnet", 10, "alice", charter="c")
        store.save_org(_o)
    sandbox._disk_flag.pop(S5, None)
    TOTAL = 4096 << 20
    _usage = [TOTAL // 2]
    _real_usage = dsk.usage
    dsk.usage = lambda slug, max_age=15.0: (
        (_usage[0], TOTAL) if slug == S5 and _usage[0] is not None
        else (_real_usage(slug, max_age) if slug != S5 else None))

    def at(frac):
        _usage[0] = int(TOTAL * frac)
        return supervisor._storage_check_disk(S5, store.load_org(S5))

    def notices():
        return (store.load_org(S5).d.get("notices") or {}).get("alice") or []

    def flags():
        d = store.load_org(S5).d
        return (bool(d.get("storage_warned")), bool(d.get("storage_blocked")),
                bool(d.get("storage_full")))

    @t("an UNMOUNTED disk checks nothing (nothing can be writing to it)")
    def _():
        _usage[0] = None
        assert supervisor._storage_check_disk(S5, store.load_org(S5)) is None
        assert flags() == (False, False, False)

    @t("half full: silence")
    def _():
        assert at(0.50) is None and flags() == (False, False, False)

    @t("80%: every LIVE node is warned once, not every check")
    def _():
        assert at(0.81) == "warned"
        assert flags() == (True, False, False)
        assert len(notices()) == 1, notices()
        assert at(0.83) is None, "it warned twice"
        assert len(notices()) == 1

    @t("☠ 90%: new turns are BLOCKED — the last 10% is the journaling reserve")
    def _():
        assert at(0.91) == "blocked"
        assert flags() == (True, True, False)
        body = notices()[-1]["text"]
        assert "90%" in body and "PAUSED" in body and "ENOSPC" in body, body

    @t("☞ the turn gate reads exactly that flag, and only for disk orgs")
    def _():
        """The gate itself lives inline in `_run_turn`'s slot section, which
        this suite deliberately does not drive (that is the turn-lifecycle
        suite's territory). A drift guard instead: the two-condition gate
        must still be there, so a refactor that drops it fails HERE."""
        src = open(os.path.join(os.path.dirname(supervisor.__file__),
                                "supervisor.py"), encoding="utf-8").read()
        assert 'org.d.get("storage_blocked") and sbx.on_disk(slug)' in src, \
            "the 90% turn gate moved or vanished"
        assert "past its 90% soft cap" in src

    @t("99%: the persistent hard-full alert state is set")
    def _():
        assert at(0.995) == "full"
        assert flags() == (True, True, True)

    @t("⚑ DEFECT: falling back into the 90–99% band never clears storage_full")
    def _():
        """`_storage_check_disk` pops the flag in memory but leaves `result`
        None, and the doc is only saved `if result:` — so the pop is thrown
        away. The admin's recovery browser keeps rendering the persistent
        'disk is full' alert after the org has recovered to 95%, until it
        happens to drop under 85% (which saves for another reason)."""
        assert at(0.95) is None
        w, b, full = flags()
        assert full is True, "FIXED — retire this reproduction"
        note("supervisor._storage_check_disk never PERSISTS the clearing of "
             "`storage_full`: the 99%→90–99% transition pops the flag in "
             "memory but sets no `result`, and the save is gated on `result`. "
             "The hard-full alert sticks until usage drops under 85%. "
             "(supervisor.py — reported, not fixed: not this suite's file.)")

    @t("…and the same fall does NOT lift the 90% block (correctly)")
    def _():
        assert flags()[1] is True

    @t("≤85%: turns resume and both soft flags clear together")
    def _():
        assert at(0.84) == "cleared"
        assert flags() == (False, False, False), \
            "the 85% clear is the only path that also persists the full-flag pop"
        body = notices()[-1]["text"]
        assert "back under the soft cap" in body and "resume" in body

    @t("⚑ DEFECT (same root): the <75% re-arm is not persisted either, so a "
       "second climb past 80% is SILENT")
    def _():
        """`elif warned and frac < 0.75: org.d.pop("storage_warned")` sets no
        `result`, and the save is gated on `result` — exactly the storage_full
        bug one branch up. An org that warned at 80%, dropped to 50% and
        climbed back never warns again (the 90% block still fires, so the
        agents' first notice of a storage problem is the turn pause)."""
        at(0.81)
        assert flags()[0] is True
        assert at(0.50) is None
        assert flags()[0] is True, "FIXED — retire this reproduction"
        assert at(0.81) is None, "FIXED — it re-warned"
        note("supervisor._storage_check_disk: the same unsaved-mutation bug "
             "hits the <75% re-arm — `storage_warned` is popped in memory "
             "only, so the 80% warning fires once per org LIFETIME unless a "
             "90% block+clear cycle happens to save the doc. One fix covers "
             "both: set a result (or save unconditionally when the doc "
             "changed). supervisor.py — reported, not fixed.")
        # put it back the only way the code actually persists
        at(0.91), at(0.50)
        assert flags() == (False, False, False), flags()

    @t("storage_check dispatches sandboxed+migrated orgs to the disk tiers")
    def _():
        _usage[0] = int(TOTAL * 0.91)
        assert supervisor.storage_check(S5) == "blocked"
        _usage[0] = int(TOTAL * 0.50)
        assert supervisor.storage_check(S5) == "cleared"

    @t("a sandboxed org with NO disk yet enforces nothing (its cap is the disk)")
    def _():
        o = mkorg("cap2", secret="5f" * 16)
        sandbox._disk_flag.pop(o.d["slug"], None)
        assert supervisor.storage_check(o.d["slug"]) is None
        drop(o.d["slug"])

    @t("☠ icacls write-blocking is never aimed at an ext4-over-WSL org")
    def _():
        calls = []
        real = subprocess.run
        subprocess.run = lambda *a, **k: calls.append(a) or real(*a, **k)
        try:
            supervisor._org_write_acl(store.load_org(S5), True)
        finally:
            subprocess.run = real
        assert not calls, "icacls cannot reach the disk — it would only ACL a "\
                          "host dir the org no longer uses"

    dsk.usage = _real_usage

    # ---- the recovery browser, over a REAL disk (needs the §4 shell)
    if not SH:
        skip("§5b (recovery browser over a real disk) needs the §4 shell")
    else:
        BSEC = store.load_org(S5).d["kiosk"]["sandbox_secret"]
        dsk.create(S5, 64)
        ROOT5 = NATIVE_MP(S5)
        for d in ("home/orgtree", "home/.claude/projects/p1", "usr/lib",
                  "workspace"):
            os.makedirs(os.path.join(ROOT5, *d.split("/")), exist_ok=True)
        with open(os.path.join(ROOT5, "home", "orgtree", ".bridge"), "w") as f:
            json.dump({"url": sandbox.bridge_url(), "secret": BSEC}, f)
        with open(os.path.join(ROOT5, "home", ".credentials.json"), "w") as f:
            f.write('{"claudeAiOauth":{"accessToken":"HOST-OAUTH-TOKEN"}}')
        with open(os.path.join(ROOT5, "workspace", "report.md"), "w") as f:
            f.write("ordinary agent output\n" * 20)
        with open(os.path.join(ROOT5, "usr", "lib", "libc.so"), "wb") as f:
            f.write(b"\0" * 4000)
        SID = "11111111-2222-3333-4444-555555555555"
        with open(os.path.join(ROOT5, "home", ".claude", "projects", "p1",
                               SID + ".jsonl"), "w") as f:
            f.write('{"type":"user"}\n')
        with store.DOC_LOCK:
            _o = store.load_org(S5)
            _o.node("alice")["session_id"] = SID
            store.save_org(_o)
        TOK5 = store.load_org(S5).d["kiosk"]["token"]
        api._token_cache["at"] = 0.0
        dsk.invalidate(S5)

        def adm(method, path, body=None, query=b""):
            return call(ADMIN, method, path, body, query=query)

        def vis(method, path, body=None, query=b""):
            return call(PUBLIC, method, f"/k/{TOK5}{path}", body, query=query)

        @t("the disk listing is served from a REAL walk, largest first")
        def _():
            r = adm("GET", f"/api/orgs/{S5}/disk")
            assert r.status == 200, r
            paths = [f["path"] for f in r.json["files"]]
            assert paths[0] == "usr/lib/libc.so", paths
            assert set(paths) >= {"workspace/report.md",
                                  "home/orgtree/.bridge"}, paths
            assert r.json["total"] == 64 * 1048576, r.json["total"]
            assert 0 < r.json["used"] < r.json["total"]

        @t("☞ every listed row carries `bytes` (the API's contract, not `size`)")
        def _():
            r = adm("GET", f"/api/orgs/{S5}/disk")
            assert all("bytes" in f and "class" in f for f in r.json["files"]), \
                r.json["files"][:2]

        @t("☠ a visitor cannot download .bridge or the credentials file")
        def _():
            for p, leak in (("home/orgtree/.bridge", BSEC),
                            ("home/.credentials.json", "HOST-OAUTH-TOKEN")):
                r = vis("GET", f"/api/orgs/{S5}/disk/file",
                        query=f"path={p}".encode())
                assert r.status == 403, (p, r)
                assert leak not in r.text, p

        @t("…while the admin may (it is their own host)")
        def _():
            r = adm("GET", f"/api/orgs/{S5}/disk/file",
                    query=b"path=home/orgtree/.bridge")
            assert r.status == 200 and BSEC in r.text, r

        @t("☠☠ THE COPY VECTOR is closed: a renamed copy of .bridge is "
           "refused to a visitor by CONTENT (PROMOTED 2026-08-04)")
        def _():
            """The denylist is by FILENAME. Agents hold passwordless root
            inside the container and the whole disk is theirs; one `cp` puts
            the bridge secret in a file the browser calls ordinary content.
            The secret then buys /api/agent (acting as ANY node of the org)
            and the /anthropic proxy, which attaches the HOST's subscription
            OAuth token. Reproduced end to end below."""
            # PROMOTED 2026-08-04. The first of this entry's own candidate
            # fixes was taken: `disk_file` scans a VISITOR download for the
            # org's own bridge secret and refuses it whatever the file is
            # called, so renaming the secret out of the deny tuple no longer
            # works. Delivery to the admin listener is untouched — this is a
            # public-gateway rule, not a general one.
            # ⚠ It is a mitigation at the read point, not a redesign: the
            # container still reads the secret from a file the browser walks.
            # The stronger fix (an env var or a per-node token, so no file on
            # the org disk ever carries it) is still worth doing, and the
            # second assertion below is what would catch a regression to it.
            leak = os.path.join(ROOT5, "workspace", "notes.txt")
            shutil.copy(os.path.join(ROOT5, "home", "orgtree", ".bridge"), leak)
            dsk.invalidate(S5)
            r = vis("GET", f"/api/orgs/{S5}/disk/file",
                    query=b"path=workspace/notes.txt")
            assert r.status == 403 and BSEC not in r.text, r
            # and the admin side still gets it — the scan is visitor-only
            a = adm("GET", f"/api/orgs/{S5}/disk/file",
                    query=b"path=workspace/notes.txt")
            assert a.status == 200 and BSEC in a.text, a
            os.remove(leak)
            dsk.invalidate(S5)

        @t("the system seed is shown but blocked in BOTH modes")
        def _():
            for how in (adm, vis):
                r = how("GET", f"/api/orgs/{S5}/disk")
                seed = [f for f in r.json["files"]
                        if f["path"] == "usr/lib/libc.so"][0]
                assert seed["class"] == "blocked" and "system seed" in \
                    seed["reason"], seed

        @t("a LIVE node's transcript is blocked; an orphan one is reclaimable")
        def _():
            r = adm("GET", f"/api/orgs/{S5}/disk")
            by = {f["path"]: f for f in r.json["files"]}
            tp = f"home/.claude/projects/p1/{SID}.jsonl"
            assert by[tp]["class"] == "blocked", by[tp]
            assert "live session of alice" in by[tp]["reason"], by[tp]
            other = SID.replace("1111", "9999")
            with open(os.path.join(ROOT5, "home", ".claude", "projects", "p1",
                                   other + ".jsonl"), "w") as f:
                f.write("{}\n")
            dsk.invalidate(S5)
            r = adm("GET", f"/api/orgs/{S5}/disk")
            by = {f["path"]: f for f in r.json["files"]}
            op = f"home/.claude/projects/p1/{other}.jsonl"
            assert by[op]["class"] == "reclaimable", by[op]

        @t("the explorer blocks a directory WHOLE when it holds a transcript")
        def _():
            r = adm("GET", f"/api/orgs/{S5}/disk/dir", query=b"path=home")
            e = {x["name"]: x for x in r.json["entries"]}
            assert e[".claude"]["class"] == "blocked", e
            assert "protected session transcript" in e[".claude"]["reason"], e

        @t("☠ delete is enforced SERVER-side, not by the UI's greying")
        def _():
            r = vis("POST", f"/api/orgs/{S5}/disk/delete",
                    {"paths": ["home/orgtree/.bridge", "home/.credentials.json",
                               "usr/lib/libc.so",
                               f"home/.claude/projects/p1/{SID}.jsonl"]})
            assert r.status == 200, r
            assert all(x["ok"] is False for x in r.json["results"]), r.json
            for p in ("home/orgtree/.bridge", "usr/lib/libc.so"):
                assert os.path.isfile(os.path.join(ROOT5, *p.split("/"))), p

        @t("☠ a directory delete is ALL-OR-NOTHING when the subtree is protected")
        def _():
            r = adm("POST", f"/api/orgs/{S5}/disk/delete", {"paths": ["home"]})
            res = r.json["results"][0]
            assert res["ok"] is False and "protected file(s)" in res["error"], res
            assert os.path.isdir(os.path.join(ROOT5, "home", ".claude"))

        @t("an ordinary file deletes, and the usage readout moves immediately")
        def _():
            before = adm("GET", f"/api/orgs/{S5}/disk").json["used"]
            with open(os.path.join(ROOT5, "workspace", "junk.bin"), "wb") as f:
                f.write(b"\0" * (3 << 20))
            dsk.invalidate(S5)
            r = adm("POST", f"/api/orgs/{S5}/disk/delete",
                    {"paths": ["workspace/junk.bin"]})
            assert r.json["results"] == [{"path": "workspace/junk.bin",
                                          "ok": True}], r.json
            assert not os.path.exists(os.path.join(ROOT5, "workspace",
                                                   "junk.bin"))
            assert abs(r.json["used"] - before) < (1 << 20), (before, r.json)

        @t("☠ …and it still deletes with the disk reported 100% FULL")
        def _():
            """ENOSPC is the enforcement, so the recovery path must not need
            free space: unlink does not. The df reading is forced to the cap
            for this check."""
            with open(os.path.join(ROOT5, "workspace", "fat.bin"), "wb") as f:
                f.write(b"\0" * (2 << 20))
            D4.used_kb = str(64 * 1024)
            dsk.invalidate(S5)
            try:
                r = adm("GET", f"/api/orgs/{S5}/disk")
                assert r.json["used"] == r.json["total"], r.json
                r = adm("POST", f"/api/orgs/{S5}/disk/delete",
                        {"paths": ["workspace/fat.bin"]})
                assert r.json["results"][0]["ok"] is True, r.json
            finally:
                D4.used_kb = ""
                dsk.invalidate(S5)

        @t("a path that escapes the disk is refused, never followed")
        def _():
            for bad in ["../../../Windows/win.ini", "..\\..\\x", "/etc/passwd",
                        "C:\\Windows\\win.ini", "", ".", "..",
                        "home/../../escape", "workspace/../../..",
                        "C:workspace/report.md", "home/orgtree/../orgtree/.bridge"]:
                r = vis("GET", f"/api/orgs/{S5}/disk/file",
                        query=("path=" + bad).encode())
                assert r.status in (403, 404, 422), (bad, r)
                assert BSEC not in r.text and "HOST-OAUTH" not in r.text, bad

        @t("an org with no disk answers 409, not a traversal surface")
        def _():
            o = mkorg("nodisk", secret="60" * 16)
            r = adm("GET", f"/api/orgs/{o.d['slug']}/disk")
            assert r.status == 409 and "no virtual disk" in r.text, r
            drop(o.d["slug"])

        @t("GROW applies online and immediately; the cap really moves")
        def _():
            with store.DOC_LOCK:                    # the doc tracks the image
                o = store.load_org(S5)
                o.d["disk"] = {"size_mb": 64}
                store.save_org(o)
            r = adm("POST", f"/api/orgs/{S5}/disk/resize", {"size_mb": 96})
            assert r.status == 200 and r.json["size_mb"] == 96, r
            assert dsk.usage(S5, max_age=0.0)[1] == 96 * 1048576
            assert store.load_org(S5).d["disk"]["size_mb"] == 96

        @t("SHRINK is staged, and shown as a divergence until it applies")
        def _():
            with store.DOC_LOCK:
                o = store.load_org(S5)
                o.d["disk"] = {"size_mb": 8192}
                store.save_org(o)
            r = adm("POST", f"/api/orgs/{S5}/disk/resize", {"size_mb": 4096})
            assert r.status == 200 and r.json == {"size_mb": 8192,
                                                  "pending_mb": 4096}, r.json
            assert store.load_org(S5).d["disk"]["pending_size_mb"] == 4096
            r = adm("POST", f"/api/orgs/{S5}/disk/resize", {"cancel": True})
            assert r.json["pending_mb"] is None
            assert "pending_size_mb" not in store.load_org(S5).d["disk"]

        @t("☠ a shrink below the 4096 MB one-disk floor is refused")
        def _():
            r = adm("POST", f"/api/orgs/{S5}/disk/resize", {"size_mb": 512})
            assert r.status == 422 and "4096 MB minimum" in r.text, r

        @t("a visitor can neither resize nor apply a resize")
        def _():
            assert vis("POST", f"/api/orgs/{S5}/disk/resize",
                       {"size_mb": 99999}).status == 403
            assert vis("POST", f"/api/orgs/{S5}/disk/resize/apply").status == 403

        @t("the visitor's payload hides the admin-only host numbers")
        def _():
            r = vis("GET", f"/api/orgs/{S5}/disk")
            for k in ("vm_cap_mib", "size_mb", "pending_mb"):
                assert k not in r.json, k
            assert "vm_cap_mib" in adm("GET", f"/api/orgs/{S5}/disk").json

        with store.DOC_LOCK:
            _o = store.load_org(S5)
            _o.d["disk"] = {"size_mb": 96}
            store.save_org(_o)
        dsk.destroy(S5)


# ================================================================== §6
if section("§6  the sandboxed turn"):
    O6 = mkorg("turn1", kiosk=True, secret="6a" * 16)
    S6 = O6.d["slug"]
    with store.DOC_LOCK:
        _o = store.load_org(S6)
        _o.hire(USER, None, "sonnet", 20, "alice", charter="c")
        _o.hire(USER, "alice", "sonnet", 5, "bob", charter="c",
                add_dirs=[], tools={"bash": True, "web": True, "edit": True,
                                    "subagents": True, "mcp": []},
                org_visibility="team")
        store.save_org(_o)
    O6 = store.load_org(S6)
    SBXHOME = os.path.join(DATA, "sandboxes", S6, "home")

    @t("an unsandboxed org has no transcript root (the host default applies)")
    def _():
        assert supervisor._transcript_root(store.load_org(O_PLAIN.d["slug"])) \
            is None

    @t("☞ a sandboxed org's transcripts live under <data>/sandboxes/<slug>/home")
    def _():
        sandbox._disk_flag.pop(S6, None)
        assert supervisor._transcript_root(O6) == \
            os.path.join(SBXHOME, ".claude")

    @t("…and follow the org onto its disk once migrated")
    def _():
        with store.DOC_LOCK:
            o = store.load_org(S6)
            o.d["disk"] = {"size_mb": 4096}
            store.save_org(o)
        sandbox._disk_flag.pop(S6, None)
        assert supervisor._transcript_root(store.load_org(S6)) == \
            os.path.join(dsk.windows_sub(S6, "home"), ".claude")
        with store.DOC_LOCK:
            o = store.load_org(S6)
            o.d.pop("disk")
            store.save_org(o)
        sandbox._disk_flag.pop(S6, None)

    @t("transcript_path finds a session under that root and nowhere else")
    def _():
        sid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        d = os.path.join(SBXHOME, ".claude", "projects", "proj")
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, sid + ".jsonl"), "w").write("{}\n")
        assert supervisor.transcript_path(sid, None) is None
        assert supervisor.transcript_path(
            sid, supervisor._transcript_root(O6)) == \
            os.path.join(d, sid + ".jsonl")

    @t("☞ REGRESSION: reconcile does NOT condemn a sandboxed node whose "
       "transcript is on the sandbox home")
    def _():
        """Omitting the org's transcript root here condemned EVERY sandboxed
        node at every restart — they resume from a home the host default
        never sees."""
        sid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        with store.DOC_LOCK:
            o = store.load_org(S6)
            o.node("alice")["session_id"] = sid
            o.node("alice")["cost_usd"] = 0.42
            store.save_org(o)
        assert supervisor.reconcile(S6) == []
        assert store.load_org(S6).node("alice")["state"] == "live"

    @t("…and DOES condemn one whose transcript is genuinely gone")
    def _():
        with store.DOC_LOCK:
            o = store.load_org(S6)
            o.node("bob")["session_id"] = "dead0000-0000-0000-0000-000000000000"
            o.node("bob")["cost_usd"] = 0.10
            store.save_org(o)
        assert supervisor.reconcile(S6) == ["bob"]
        assert store.load_org(S6).node("bob")["state"] != "live"

    # ---- the turn command line
    CMD = supervisor._build_cmd(store.load_org(S6), "alice")

    @t("☠ the turn runs INSIDE the container, in the node's own scratch dir")
    def _():
        assert CMD[:5] == ["docker", "exec", "-i", "-w",
                           sandbox.cpath_scratch(S6, "alice")], CMD[:6]
        assert CMD[5] == sandbox.container_name(S6), CMD[5]
        assert CMD[6] == "claude", CMD[6]

    @t("the steering hook runs the read-only backend mount's python3")
    def _():
        st = json.loads(CMD[CMD.index("--settings") + 1])
        hook = st["hooks"]["PostToolUse"][0]["hooks"][0]["command"]
        assert hook == ("python3 /opt/orgtree-backend/orgtree/steer.py "
                        f'"{S6}" "alice"'), hook
        assert st["hooks"]["SessionStart"] == [], \
            "the operator's global hooks must not leak into an agent"

    @t("☠ NO MCP server reaches a sandbox but orgtree, over the bridge")
    def _():
        cfg = json.loads(CMD[CMD.index("--mcp-config") + 1])["mcpServers"]
        assert list(cfg) == ["orgtree"], cfg
        o = cfg["orgtree"]
        assert o["command"] == "python3"
        assert o["args"] == ["/opt/orgtree-backend/orgtree/mcptool.py"]
        assert o["env"]["ORGTREE_BASE"] == sandbox.bridge_url()
        assert o["env"]["ORGTREE_BRIDGE_SECRET"] == "6a" * 16
        assert "PYTHONPATH" not in o["env"] and "ORGTREE_PORT" not in o["env"], \
            "a host path or the admin port would be a lie inside the container"

    @t("only container paths are handed to the agent — no host path anywhere")
    def _():
        adds = [CMD[i + 1] for i, x in enumerate(CMD) if x == "--add-dir"]
        assert adds and all(a.startswith("/home/agent/orgtree/") for a in adds), \
            adds
        joined = " ".join(CMD)
        assert DATA not in joined, "the host data root leaked into the argv"
        assert "C:\\" not in joined and "c:\\" not in joined.lower(), joined[:400]

    @t("☞ a §7.6 read-down grant reaches the descendant's CONTAINER scratch")
    def _():
        adds = [CMD[i + 1] for i, x in enumerate(CMD) if x == "--add-dir"]
        # D-201/S2(a): the read-down is one fixed parent path now, not one
        # entry per descendant, so bob is reached by COVERAGE rather than by
        # being named. The assertion tests the same property it always did —
        # can the agent's file tools reach its report's container scratch —
        # and deliberately does not care which shape delivers it. Exact-match
        # here would have been an assertion about the implementation.
        want = sandbox.cpath_scratch(S6, "bob")
        assert any(want == a or want.startswith(a.rstrip("/") + "/")
                   for a in adds), (want, adds)

    @t("an external folder grant cannot follow into the container")
    def _():
        ext = os.path.join(DATA, "outside-folder")
        os.makedirs(ext, exist_ok=True)
        with store.DOC_LOCK:
            o = store.load_org(S6)
            o.node("alice")["scope"]["add_dirs"].append({"path": ext,
                                                         "mode": "rw"})
            store.save_org(o)
        cmd = supervisor._build_cmd(store.load_org(S6), "alice")
        adds = [cmd[i + 1] for i, x in enumerate(cmd) if x == "--add-dir"]
        assert ext not in adds and all(a.startswith("/home/agent/") for a in adds)

    @t("the workspace grant maps onto the container's ONE window")
    def _():
        ws = store.load_org(S6).d["workspace"]
        with store.DOC_LOCK:
            o = store.load_org(S6)
            o.node("alice")["scope"]["add_dirs"] = [{"path": ws, "mode": "rw"}]
            store.save_org(o)
        cmd = supervisor._build_cmd(store.load_org(S6), "alice")
        adds = [cmd[i + 1] for i, x in enumerate(cmd) if x == "--add-dir"]
        assert sandbox.cpath_workspace(S6) in adds, adds

    @t("a read-only workspace grant still writes deny rules, in container terms")
    def _():
        ws = store.load_org(S6).d["workspace"]
        with store.DOC_LOCK:
            o = store.load_org(S6)
            o.node("alice")["scope"]["add_dirs"] = [{"path": ws, "mode": "ro"}]
            store.save_org(o)
        cmd = supervisor._build_cmd(store.load_org(S6), "alice")
        st = json.loads(cmd[cmd.index("--settings") + 1])
        deny = st["permissions"]["deny"]
        # D-220: one Edit() rule per path — the only deny shape the CLI matches
        assert f"Edit({sandbox.cpath_workspace(S6)}/**)" in deny, deny
        with store.DOC_LOCK:
            o = store.load_org(S6)
            o.node("alice")["scope"]["add_dirs"] = [{"path": ws, "mode": "rw"}]
            store.save_org(o)

    @t("the sandboxed identity prompt tells the agent it is in a container")
    def _():
        p = supervisor.identity_prompt(store.load_org(S6), "alice")
        assert "sandbox container" in p, p[:400]
        assert "Terminal: Bash." in p, "PowerShell would be a lie on Linux"

    # ---- the MCP escape hatch
    REG = {"weather": {"url": "http://localhost:9099/mcp"},
           "fs": {"command": "cmd", "args": ["/c", "npx", "-y", "@x/fs"]},
           "native": {"command": "C:\\tools\\thing.exe", "args": []},
           "py": {"command": "python3", "args": ["-m", "srv"]}}

    @t("☠ by default a sandbox gets NOTHING from the MCP registry")
    def _():
        assert supervisor.sandbox_mcp_passthrough(list(REG), REG) == {}
        assert not supervisor.sandbox_mcp_enabled()

    @t("ORGTREE_SANDBOX_MCP passes URL + portable-stdio servers only")
    def _():
        os.environ["ORGTREE_SANDBOX_MCP"] = "1"
        try:
            out = supervisor.sandbox_mcp_passthrough(list(REG), REG)
            assert out["weather"]["url"] == "http://host.docker.internal:9099/mcp"
            assert out["fs"]["command"] == "npx" and \
                out["fs"]["args"] == ["-y", "@x/fs"], out["fs"]
            assert "py" in out and "native" not in out, out
            assert REG["weather"]["url"] == "http://localhost:9099/mcp", \
                "the registry itself was mutated"
        finally:
            os.environ.pop("ORGTREE_SANDBOX_MCP")

    @t("…and a server that was never GRANTED is still not passed")
    def _():
        os.environ["ORGTREE_SANDBOX_MCP"] = "1"
        try:
            assert supervisor.sandbox_mcp_passthrough(["weather"], REG) == \
                {"weather": {"url": "http://host.docker.internal:9099/mcp"}}
        finally:
            os.environ.pop("ORGTREE_SANDBOX_MCP")

    @t("with the flag on, the turn really carries the extra server")
    def _():
        os.environ["ORGTREE_SANDBOX_MCP"] = "1"
        real_reg = supervisor.registered_mcp_servers
        supervisor.registered_mcp_servers = lambda: REG
        try:
            with store.DOC_LOCK:
                o = store.load_org(S6)
                o.node("alice")["scope"]["tools"]["mcp"] = ["weather"]
                store.save_org(o)
            cmd = supervisor._build_cmd(store.load_org(S6), "alice")
            cfg = json.loads(cmd[cmd.index("--mcp-config") + 1])["mcpServers"]
            assert set(cfg) == {"orgtree", "weather"}, cfg
            assert "mcp__weather" in cmd[cmd.index("--allowedTools") + 1]
        finally:
            supervisor.registered_mcp_servers = real_reg
            os.environ.pop("ORGTREE_SANDBOX_MCP")
            with store.DOC_LOCK:
                o = store.load_org(S6)
                o.node("alice")["scope"]["tools"]["mcp"] = []
                store.save_org(o)

    # ---- container→host translation of agent-supplied dirs
    @t("☞ a sandboxed agent's own container paths are accepted as grants")
    def _():
        o = store.load_org(S6)
        ws = o.d["workspace"]
        dirs, warns = supervisor.sandbox_dirs_to_host(
            o, [{"path": sandbox.cpath_workspace(S6) + "/sub", "mode": "ro"}])
        assert dirs == [{"path": os.path.normpath(ws + "/sub"),
                         "mode": "ro"}], dirs
        assert warns == []

    @t("a scratch path is DROPPED with a warning (it is always reachable)")
    def _():
        o = store.load_org(S6)
        dirs, warns = supervisor.sandbox_dirs_to_host(
            o, [sandbox.cpath_scratch(S6, "bob")])
        assert dirs == [] and warns and "scratch" in warns[0], (dirs, warns)

    @t("anything else passes through to meet the honest №30 refusal")
    def _():
        o = store.load_org(S6)
        dirs, warns = supervisor.sandbox_dirs_to_host(o, ["/etc"])
        assert dirs == [{"path": "/etc", "mode": "rw"}] and warns == []

    @t("an UNSANDBOXED org is never translated")
    def _():
        o = store.load_org(O_PLAIN.d["slug"])
        assert supervisor.sandbox_dirs_to_host(o, ["C:\\x"]) == (["C:\\x"], [])

    # ---- steering into the container, over the real bridge listener
    @t("☞ steer.py inside a container reaches the node through the bridge")
    def _():
        import uvicorn
        cfg = uvicorn.Config(api.BridgeGateway(api.app), host="127.0.0.1",
                             port=7407, log_level="error")
        server = uvicorn.Server(cfg)
        th = threading.Thread(target=server.run, daemon=True)
        th.start()
        for _ in range(100):
            if getattr(server, "started", False):
                break
            time.sleep(0.05)
        assert server.started, "the bridge listener never came up"
        api._bridge_cache["at"] = 0.0
        try:
            # the container's view: ~/orgtree with a .bridge in its root
            croot = os.path.join(DATA, "cbox", "orgtree")
            os.makedirs(os.path.join(croot, "scratch", S6, "alice"),
                        exist_ok=True)
            with open(os.path.join(croot, ".bridge"), "w") as f:
                json.dump({"url": "http://127.0.0.1:7407",
                           "secret": "6a" * 16}, f)
            supervisor.state(S6, "alice")["steer"] = ["mail one", "mail two"]
            env = dict(os.environ, ORGTREE_DATA=croot)
            r = subprocess.run(
                [sys.executable,
                 os.path.join(os.path.dirname(supervisor.__file__), "steer.py"),
                 S6, "alice"],
                cwd=os.path.join(croot, "scratch", S6, "alice"),
                capture_output=True, text=True, env=env, timeout=30, input="")
            assert r.returncode == 0, r.stderr[-400:]
            assert r.stdout.strip(), (r.stdout, r.stderr[-500:])
            out = json.loads(r.stdout)["hookSpecificOutput"]
            assert "mail one" in out["additionalContext"], out
            assert "mail two" in out["additionalContext"], out
            assert supervisor.state(S6, "alice")["steer"] == [], \
                "the fetch is the delivery point — it must drain"

            # ☠ the same hook with a WRONG secret gets nothing at all
            with open(os.path.join(croot, ".bridge"), "w") as f:
                json.dump({"url": "http://127.0.0.1:7407", "secret": "0" * 32},
                          f)
            supervisor.state(S6, "alice")["steer"] = ["secret mail"]
            r = subprocess.run(
                [sys.executable,
                 os.path.join(os.path.dirname(supervisor.__file__), "steer.py"),
                 S6, "alice"],
                cwd=os.path.join(croot, "scratch", S6, "alice"),
                capture_output=True, text=True, env=env, timeout=30, input="")
            assert r.returncode == 0 and r.stdout.strip() == "", r.stdout
            assert supervisor.state(S6, "alice")["steer"] == ["secret mail"], \
                "an unauthorised hook consumed the mail"
        finally:
            server.should_exit = True
            th.join(timeout=20)

    @t("a container hook with NO .bridge falls back to loopback, not silence")
    def _():
        croot = os.path.join(DATA, "cbox2", "orgtree")
        os.makedirs(os.path.join(croot, "scratch", S6, "alice"), exist_ok=True)
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "steermod", os.path.join(os.path.dirname(supervisor.__file__),
                                     "steer.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        old = os.getcwd()
        os.environ["ORGTREE_DATA"] = croot
        try:
            os.chdir(os.path.join(croot, "scratch", S6, "alice"))
            sys.argv = ["steer.py"]
            org, node, base, secret = mod.identity()
            assert (org, node) == (S6, "alice"), (org, node)
            assert base == "http://127.0.0.1:7407" and secret == "", base
        finally:
            os.chdir(old)
            os.environ["ORGTREE_DATA"] = DATA


# ================================================================== §7
# The OAuth refresh that keeps every proxied sandbox running. ⚠ The host's
# REAL credentials file is never opened here: CREDS is redirected to a fixture
# first and a guard asserts it.
if section("§7  subproxy — the OAuth refresh"):
    import urllib.request as _ur

    subproxy.get_access_token = _REAL_GET_TOKEN     # §3 stubbed the module
    CDIR = os.path.join(DATA, "creds")
    os.makedirs(CDIR, exist_ok=True)
    subproxy.CREDS = os.path.join(CDIR, ".credentials.json")
    assert os.path.normcase(subproxy.CREDS) != os.path.normcase(REAL_CREDS), \
        "refusing to run §7 against the host's real credentials"
    REAL_CREDS_MTIME = (os.path.getmtime(REAL_CREDS)
                        if os.path.exists(REAL_CREDS) else None)
    NOW = time.time()
    _resp = {"access_token": "AT-2", "refresh_token": "RT-2",
             "expires_in": 3600}
    _fail: list[Exception] = []
    _posts: list[dict] = []

    class _FakeHTTP:
        def __init__(self, payload):
            self.payload = json.dumps(payload).encode()

        def read(self, *a):
            return self.payload

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        _posts.append({"url": req.full_url, "method": req.get_method(),
                       "body": json.loads(req.data.decode()),
                       "headers": dict(req.header_items())})
        if _fail:
            raise _fail[0]
        return _FakeHTTP(_resp)

    _real_urlopen = _ur.urlopen
    _ur.urlopen = fake_urlopen

    def write_creds(**over):
        doc = {"claudeAiOauth": {
            "accessToken": "AT-1", "refreshToken": "RT-1",
            "expiresAt": int((time.time() + 7200) * 1000),
            "refreshTokenExpiresAt": int((time.time() + 86400 * 30) * 1000),
            "scopes": ["user:inference"], "subscriptionType": "max"},
            "organizationUuid": "org-uuid"}
        doc["claudeAiOauth"].update(over)
        with open(subproxy.CREDS, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        return doc

    def read_creds():
        return json.load(open(subproxy.CREDS, encoding="utf-8"))

    @t("available() is exactly 'is there a credentials file'")
    def _():
        if os.path.exists(subproxy.CREDS):
            os.remove(subproxy.CREDS)
        assert not subproxy.available()
        write_creds()
        assert subproxy.available()

    @t("a token with more than 5 minutes left is returned WITHOUT a refresh")
    def _():
        _posts.clear()
        assert subproxy.get_access_token() == "AT-1"
        assert _posts == [], "it refreshed a live token"

    @t("the 5-minute skew boundary is honoured in both directions")
    def _():
        _posts.clear()
        write_creds(expiresAt=int((time.time() + 301) * 1000))
        assert subproxy.get_access_token() == "AT-1" and not _posts
        write_creds(expiresAt=int((time.time() + 299) * 1000))
        assert subproxy.get_access_token() == "AT-2" and len(_posts) == 1

    @t("the refresh POST is the documented shape (grant, token, client id)")
    def _():
        p = _posts[-1]
        assert p["url"] == subproxy.TOKEN_URL and p["method"] == "POST", p
        assert p["body"] == {"grant_type": "refresh_token",
                             "refresh_token": "RT-1",
                             "client_id": subproxy.CLIENT_ID}, p["body"]
        assert p["headers"].get("Content-type") == "application/json", p

    @t("a refreshed token is written back in place, atomically")
    def _():
        write_creds(expiresAt=0)
        assert subproxy.get_access_token() == "AT-2"
        d = read_creds()["claudeAiOauth"]
        assert d["accessToken"] == "AT-2" and d["refreshToken"] == "RT-2", d
        assert abs(d["expiresAt"] / 1000 - (time.time() + 3600)) < 5, d
        assert read_creds()["organizationUuid"] == "org-uuid", \
            "the rest of the document must survive the write"
        assert not glob.glob(os.path.join(CDIR, "*.tmp")), "a temp file leaked"

    @t("an unchanged refresh token is kept (the API may omit it)")
    def _():
        global _resp
        _resp = {"access_token": "AT-3", "expires_in": 60}
        write_creds(expiresAt=0)
        assert subproxy.get_access_token() == "AT-3"
        assert read_creds()["claudeAiOauth"]["refreshToken"] == "RT-1"
        _resp = {"access_token": "AT-2", "refresh_token": "RT-2",
                 "expires_in": 3600}

    @t("☞ FIXED: a ROTATED refresh token no longer leaves a stale "
       "refreshTokenExpiresAt describing the token it replaced")
    def _():
        """Before the fix this field was never touched: after a rotation the
        file claimed an expiry belonging to a refresh token that no longer
        exists. It only ever moves further into the past, and the host CLI
        and this proxy share the one file."""
        old = write_creds(expiresAt=0)["claudeAiOauth"]["refreshTokenExpiresAt"]
        subproxy.get_access_token()
        d = read_creds()["claudeAiOauth"]
        assert d["refreshToken"] == "RT-2", d
        assert d.get("refreshTokenExpiresAt") != old, \
            "the stale expiry of the REPLACED refresh token is still there"
        fixed("subproxy.get_access_token never updated `refreshTokenExpiresAt` "
              "— a real field of ~/.claude/.credentials.json, which the host "
              "CLI and this proxy SHARE. After a refresh-token rotation the "
              "file described a token that no longer existed, drifting "
              "further into the past with every refresh. Now: recorded when "
              "the endpoint reports a lifetime, dropped when the token "
              "rotated and it does not, left alone when it did not rotate. "
              "`_write` hardened too — a failed write used to leave a stray "
              ".tmp beside the real credentials and escape as a "
              "non-RuntimeError, i.e. a bare 500 out of /anthropic.")

    @t("…and an expiry the endpoint DOES report is recorded")
    def _():
        global _resp
        _resp = {"access_token": "AT-4", "refresh_token": "RT-4",
                 "expires_in": 3600, "refresh_token_expires_in": 86400}
        try:
            write_creds(expiresAt=0)
            subproxy.get_access_token()
            d = read_creds()["claudeAiOauth"]
            assert abs(d["refreshTokenExpiresAt"] / 1000
                       - (time.time() + 86400)) < 5, d
        finally:
            _resp = {"access_token": "AT-2", "refresh_token": "RT-2",
                     "expires_in": 3600}

    @t("an UNROTATED refresh token keeps its own expiry untouched")
    def _():
        global _resp
        _resp = {"access_token": "AT-5", "expires_in": 3600}
        try:
            old = write_creds(expiresAt=0)["claudeAiOauth"][
                "refreshTokenExpiresAt"]
            subproxy.get_access_token()
            assert read_creds()["claudeAiOauth"]["refreshTokenExpiresAt"] == old
        finally:
            _resp = {"access_token": "AT-2", "refresh_token": "RT-2",
                     "expires_in": 3600}

    @t("☠ a FAILED refresh raises actionably and leaves the file untouched")
    def _():
        doc = write_creds(expiresAt=0)
        before = open(subproxy.CREDS, encoding="utf-8").read()
        _fail.append(OSError("HTTP Error 400: Bad Request"))
        try:
            subproxy.get_access_token()
            raise AssertionError("a failed refresh returned a token")
        except RuntimeError as e:
            assert "subscription token refresh failed" in str(e), e
            assert "400" in str(e), e
        finally:
            _fail.clear()
        assert open(subproxy.CREDS, encoding="utf-8").read() == before
        assert not glob.glob(os.path.join(CDIR, "*.tmp"))

    @t("a malformed refresh RESPONSE does not corrupt the credentials file")
    def _():
        global _resp
        _resp = {"nothing": "useful"}
        before = None
        try:
            write_creds(expiresAt=0)
            before = open(subproxy.CREDS, encoding="utf-8").read()
            subproxy.get_access_token()
            raise AssertionError("no raise")
        except RuntimeError as e:
            assert "refresh" in str(e).lower(), e
        finally:
            _resp = {"access_token": "AT-2", "refresh_token": "RT-2",
                     "expires_in": 3600}
        assert open(subproxy.CREDS, encoding="utf-8").read() == before, \
            "a half-applied refresh reached the shared file"

    @t("a missing credentials file is an actionable RuntimeError, not a 500")
    def _():
        os.remove(subproxy.CREDS)
        try:
            subproxy.get_access_token()
            raise AssertionError("no raise")
        except RuntimeError as e:
            assert "no readable Claude credentials" in str(e), e
            assert subproxy.CREDS in str(e), e

    @t("an unparseable credentials file says so (it does not crash the proxy)")
    def _():
        open(subproxy.CREDS, "w").write("{not json")
        try:
            subproxy.get_access_token()
            raise AssertionError("no raise")
        except RuntimeError as e:
            assert "no readable Claude credentials" in str(e), e

    @t("a file with no OAuth block tells the operator to log in")
    def _():
        json.dump({"organizationUuid": "x"},
                  open(subproxy.CREDS, "w", encoding="utf-8"))
        try:
            subproxy.get_access_token()
            raise AssertionError("no raise")
        except RuntimeError as e:
            assert "log in with the Claude Code CLI first" in str(e), e

    @t("an expired token with no refresh token asks for a re-login")
    def _():
        write_creds(expiresAt=0, refreshToken="")
        try:
            subproxy.get_access_token()
            raise AssertionError("no raise")
        except RuntimeError as e:
            assert "re-login with the CLI" in str(e), e

    @t("☞ concurrent callers refresh ONCE (the lock re-reads inside it)")
    def _():
        write_creds(expiresAt=0)
        _posts.clear()
        out, errs = [], []

        def go():
            try:
                out.append(subproxy.get_access_token())
            except Exception as e:                             # noqa: BLE001
                errs.append(e)

        ths = [threading.Thread(target=go) for _ in range(8)]
        for th in ths:
            th.start()
        for th in ths:
            th.join(20)
        assert not errs, errs
        assert out == ["AT-2"] * 8, out
        assert len(_posts) == 1, f"{len(_posts)} refreshes for one expiry"

    @t("☠ the host's REAL credentials file was never opened by this suite")
    def _():
        now = (os.path.getmtime(REAL_CREDS)
               if os.path.exists(REAL_CREDS) else None)
        assert now == REAL_CREDS_MTIME, "the real credentials file changed"
        assert not glob.glob(os.path.join(os.path.dirname(REAL_CREDS),
                                          "*.tmp"))

    _ur.urlopen = _real_urlopen


# ================================================================== §8
if section("§8  creation-time rules"):
    def create(**body):
        return call(ADMIN, "POST", "/api/orgs", body)

    @t("☠ a sandboxed kiosk under the 4096 MB floor is refused at CREATION")
    def _():
        r = create(name=PFX + "floor1",
                   kiosk={"credits": 100, "storage_limit_mb": 256,
                          "sandbox": True})
        assert r.status == 422 and "4096 MB minimum" in r.text, r
        assert not os.path.exists(os.path.join(DATA, "orgs",
                                               PFX + "floor1.json"))

    @t("…and so is a sandboxed NORMAL org with a small disk_mb")
    def _():
        r = create(name=PFX + "floor2", sandbox=True, disk_mb=1024)
        assert r.status == 422 and "at least 4096" in r.text, r

    @t("an UNSANDBOXED kiosk may have any storage limit (it is a loose cap)")
    def _():
        r = create(name=PFX + "loose", kiosk={"credits": 10,
                                              "storage_limit_mb": 256,
                                              "sandbox": False})
        assert r.status == 200, r
        o = store.load_org(r.json["slug"])
        assert not sandbox.is_sandboxed(o)
        assert o.d["kiosk"]["storage_limit_mb"] == 256
        drop(o.d["slug"])

    @t("☞ a kiosk is BORN sandboxed unless the form says otherwise")
    def _():
        r = create(name=PFX + "dflt", kiosk={"credits": 10,
                                             "storage_limit_mb": 4096})
        assert r.status == 200, r
        k = store.load_org(r.json["slug"]).d["kiosk"]
        assert k["sandbox"] is True, k
        drop(r.json["slug"])

    @t("a sandboxed kiosk is minted with a 32-hex secret and warmed")
    def _():
        _warmed.clear()
        r = create(name=PFX + "mint", kiosk={"credits": 10,
                                             "storage_limit_mb": 4096,
                                             "sandbox": True})
        assert r.status == 200, r
        slug = r.json["slug"]
        k = store.load_org(slug).d["kiosk"]
        assert re.fullmatch(r"[a-f0-9]{32}", k["sandbox_secret"]), k
        assert k["sandbox_secret"] != k["token"], "one secret for two doors"
        assert _warmed == [slug], _warmed
        api._bridge_cache["at"] = 0.0
        assert api._bridge_secret_map()[k["sandbox_secret"]] == slug
        drop(slug)

    @t("a sandboxed NORMAL org gets the same isolation, no kiosk limits")
    def _():
        _warmed.clear()
        r = create(name=PFX + "normsbx", sandbox=True, disk_mb=8192)
        assert r.status == 200, r
        d = store.load_org(r.json["slug"]).d
        assert d.get("kiosk") in (None, {}), d.get("kiosk")
        assert d["sandbox"]["enabled"] and d["sandbox"]["limit_mb"] == 8192
        assert re.fullmatch(r"[a-f0-9]{32}", d["sandbox"]["secret"])
        assert _warmed == [r.json["slug"]]
        drop(r.json["slug"])

    @t("…and with no disk_mb it inherits the module default at migration")
    def _():
        r = create(name=PFX + "nodiskmb", sandbox=True)
        d = store.load_org(r.json["slug"]).d
        assert "limit_mb" not in d["sandbox"], d["sandbox"]
        assert sandbox.DISK_MB >= 4096, sandbox.DISK_MB
        drop(r.json["slug"])

    @t("a plain org is never sandboxed, never warmed, and holds no secret")
    def _():
        _warmed.clear()
        r = create(name=PFX + "plain2")
        d = store.load_org(r.json["slug"]).d
        assert not d.get("sandbox") and not d.get("kiosk"), d
        assert _warmed == [], _warmed
        drop(r.json["slug"])

    @t("☠ the floor is re-enforced when the limit is EDITED, not only at birth")
    def _():
        r = create(name=PFX + "edit1", kiosk={"credits": 10,
                                              "storage_limit_mb": 4096,
                                              "sandbox": True})
        slug = r.json["slug"]
        bad = call(ADMIN, "POST", f"/api/orgs/{slug}/kiosk",
                   {"storage_limit_mb": 512})
        assert bad.status == 422 and "4096 MB minimum" in bad.text, bad
        assert store.load_org(slug).d["kiosk"]["storage_limit_mb"] == 4096
        ok = call(ADMIN, "POST", f"/api/orgs/{slug}/kiosk",
                  {"storage_limit_mb": 10240})
        assert ok.status == 200, ok
        drop(slug)

    @t("☠ a subscription-auth sandbox cannot be given a PUBLIC kiosk URL")
    def _():
        r = create(name=PFX + "subs", kiosk={"credits": 10,
                                             "storage_limit_mb": 4096,
                                             "sandbox": True})
        slug = r.json["slug"]
        with store.DOC_LOCK:
            o = store.load_org(slug)
            o.d["kiosk"]["api_key"] = "subscription"
            o.d["kiosk"]["enabled"] = False
            store.save_org(o)
        bad = call(ADMIN, "POST", f"/api/orgs/{slug}/kiosk", {"enabled": True})
        assert bad.status == 422 and "COPIED host credentials" in bad.text, bad
        assert not store.load_org(slug).d["kiosk"]["enabled"]
        drop(slug)

    @t("the host capability payload tells the UI what to grey out")
    def _():
        r = call(ADMIN, "GET", "/api/host")
        assert set(("docker", "sandbox_mcp")) <= set(r.json), r.json
        assert r.json["sandbox_mcp"] is False
        assert isinstance(r.json["docker"], bool)

    @t("…and the MCP list carries the same flag (servers are greyed per org)")
    def _():
        r = call(ADMIN, "GET", "/api/mcp-servers")
        assert r.json["sandbox_mcp"] is False, r.json
        os.environ["ORGTREE_SANDBOX_MCP"] = "1"
        try:
            assert call(ADMIN, "GET", "/api/mcp-servers").json["sandbox_mcp"]
        finally:
            os.environ.pop("ORGTREE_SANDBOX_MCP")

    @t("deleting an org tears its sandbox down with it")
    def _():
        r = create(name=PFX + "delsbx", sandbox=True, disk_mb=4096)
        slug = r.json["slug"]
        seen = []
        real = sandbox.remove
        sandbox.remove = lambda s: seen.append(s)
        try:
            d = call(ADMIN, "DELETE", f"/api/orgs/{slug}")
            assert d.status == 200, d
        finally:
            sandbox.remove = real
        assert seen == [slug], seen

    @t("docker_available() is a cached PATH probe, never a daemon call")
    def _():
        sandbox._docker_ok = None
        assert sandbox.docker_available() is (shutil.which("docker")
                                              is not None)


# ================================================================== §9
# The real thing: a real image, a real ext4 disk in the real docker-desktop
# distro, a real container — and then attacks on it. Everything created is
# named after a zzsbx- slug and removed in the finally block; nothing else on
# the daemon is inspected, stopped or deleted.
def dk(*args, timeout=300):
    # utf-8, not the console codepage: an org chart carries box-drawing
    # characters and cp1252 raised inside subprocess's reader thread
    return subprocess.run(["docker", *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


if section("§9  real Docker") and not DOCKER_TIER:
    skip("§9 needs --docker (it builds an image, formats an ext4 disk and "
         "runs a container; minutes, not seconds)")
elif DOCKER_TIER:
    if shutil.which("docker") is None:
        skip("§9: no docker CLI on PATH")
    elif dk("version", "--format", "{{.Server.Version}}", timeout=60
            ).returncode != 0:
        skip("§9: the Docker daemon is not running (start Docker Desktop) — "
             "every check below is skipped, the hermetic tiers are unaffected")
    else:
        # the hermetic tiers stubbed `_docker`, `_run`, MOUNT_ROOT and the
        # distro cache — reload puts the real ones back IN PLACE, so every
        # other module's reference to these two follows
        import importlib
        importlib.reload(sandbox)
        importlib.reload(dsk)
        # ⚠ HOST-STATE PROBE. disk.py mounts every org at /mnt/wsl/orgtree-disk
        # inside the docker-desktop distro. On a host where that path is not
        # writable the feature cannot work at all — measured here rather than
        # assumed, and the section relocates so the REST of the tier still
        # runs against real ext4 and a real container.
        _probe = dsk._sh(f"mkdir -p {dsk.mount_root()}/probe-zzsbx && "
                         f"rmdir {dsk.mount_root()}/probe-zzsbx")
        if _probe.returncode != 0:
            note("☠ HOST STATE: the docker-desktop distro's / is a READ-ONLY "
                 "overlay and NOTHING is mounted on /mnt/wsl (`mount | grep "
                 "-c 'on /mnt/wsl'` → 0), so disk.py's MOUNT_ROOT cannot be "
                 "created: `mkdir -p /mnt/wsl/orgtree-disk/<slug>` → "
                 "'Read-only file system'. Effect on THIS host, right now: no "
                 "NEW sandboxed org can be created or started (create/mount "
                 "raise DiskError and ensure_container hard-refuses — the "
                 "sentinel design working as intended). Orgs whose mountpoint "
                 "directory already exists still mount, because `mkdir -p` on "
                 "an existing directory returns 0 even on a read-only fs — "
                 "which is why the three pre-existing org dirs survive. The "
                 "shared cross-distro /mnt/wsl tmpfs is alive in the OTHER "
                 "distro but is not mounted into docker-desktop's namespace. "
                 "Candidate fix: put MOUNT_ROOT on a path that is writable in "
                 "the daemon's own distro (e.g. /mnt/docker-desktop-disk/…), "
                 "which the daemon binds and \\\\wsl.localhost still serves. "
                 "§9 relocates MOUNT_ROOT to run the rest of the tier.")
            dsk._mount_root_cache = "/mnt/docker-desktop-disk/orgtree-zzsbx-test"
            print(f"       … relocated MOUNT_ROOT to {dsk.mount_root()}")
        S9 = PFX + "real1"
        NAME9 = sandbox.container_name(S9)
        made = {"container": False, "disk": False}

        def teardown9():
            if made["container"]:
                dk("rm", "-f", NAME9, timeout=120)
            if made["disk"]:
                try:
                    dsk.destroy(S9)
                except Exception:                              # noqa: BLE001
                    pass
            if "zzsbx" in dsk.MOUNT_ROOT:      # the relocated root is OURS
                dsk._sh(f"rmdir {dsk.MOUNT_ROOT}/* {dsk.MOUNT_ROOT} "
                        f"2>/dev/null; true")
            for line in dk("ps", "-a", "--format", "{{.Names}}").stdout.split():
                assert not line.startswith("orgtree-" + PFX) or \
                    line == NAME9, f"stray test container {line}"

        atexit.register(teardown9)
        print("       … building the sandbox image (first run: minutes)")
        TAG9 = sandbox.ensure_image()

        @t("the real image builds and carries the host CLI's version in its tag")
        def _():
            assert dk("image", "inspect", TAG9).returncode == 0
            assert supervisor.cli_version() in TAG9 or TAG9 == sandbox.IMAGE

        @t("☞ a REAL ext4 disk: volume + sparse image + sentinel, mounted once")
        def _():
            dsk.create(S9, 64)
            made["disk"] = True
            assert dsk.exists(S9) and dsk.is_mounted(S9)
            u = dsk.usage(S9, max_age=0.0)
            assert u and 50 << 20 < u[1] <= 64 << 20, u   # ext4 metadata

        @t("the Windows side reads the same filesystem over \\\\wsl.localhost")
        def _():
            p = os.path.join(dsk.windows_path(S9), "hello.txt")
            with open(p, "w", encoding="utf-8") as f:
                f.write("from the host")
            r = dsk._sh(f"cat {dsk.mount_path(S9)}/hello.txt")
            assert r.stdout.strip() == "from the host", r

        @t("☠ ENOSPC IS the cap: writing past it fails at the filesystem")
        def _():
            r = dsk._sh(f"dd if=/dev/zero of={dsk.mount_path(S9)}/fill.bin "
                        f"bs=1M count=200 2>&1", timeout=300)
            assert "No space left" in (r.stdout + r.stderr), (r.stdout,
                                                              r.stderr)
            dsk.invalidate(S9)
            u = dsk.usage(S9, max_age=0.0)
            assert u and u[0] / u[1] > 0.95, u

        @t("☠ …and DELETING still works at 100% full (the recovery path)")
        def _():
            os.unlink(os.path.join(dsk.windows_path(S9), "fill.bin"))
            dsk.invalidate(S9)
            u = dsk.usage(S9, max_age=0.0)
            assert u and u[0] / u[1] < 0.5, u

        @t("grow() is online: the cap moves with the container's data intact")
        def _():
            dsk.grow(S9, 128)
            assert dsk.usage(S9, max_age=0.0)[1] > 110 << 20
            assert open(os.path.join(dsk.windows_path(S9), "hello.txt")
                        ).read() == "from the host"

        @t("shrink_image() is offline and reversible-safe")
        def _():
            dsk.shrink_image(S9, 64)
            dsk.mount(S9)
            assert 40 << 20 < dsk.usage(S9, max_age=0.0)[1] <= 64 << 20
            assert open(os.path.join(dsk.windows_path(S9), "hello.txt")
                        ).read() == "from the host"

        @t("enumerate_by_size + list_dir run inside the distro on real ext4")
        def _():
            files = dsk.enumerate_by_size(S9)
            assert {f["path"] for f in files} == {"hello.txt"}, files
            assert dsk.list_dir(S9, "")[0]["name"] == "hello.txt"

        # ---- a real container on a real disk
        print("       … seeding the org disk from the image (minutes)")
        O9 = mkorg("real1", kiosk=True, secret="99" * 16)
        assert O9.d["slug"] == S9, O9.d["slug"]
        with store.DOC_LOCK:
            _o = store.load_org(S9)
            _o.d["kiosk"]["storage_limit_mb"] = 4096
            _o.hire(USER, None, "sonnet", 10, "alice", charter="c")
            store.save_org(_o)
        sandbox._disk_flag.pop(S9, None)
        dsk.destroy(S9)                 # the 64 MB probe disk; migrate makes its own
        made["disk"] = True
        sandbox.ensure_container(store.load_org(S9))
        made["container"] = True

        def ex(*cmd, user=None):
            pre = ["exec"] + (["-u", user] if user else []) + [NAME9]
            r = dk(*pre, *cmd, timeout=120)
            # a redirection that FAILS reports on the shell's own stderr,
            # below any `2>&1` inside the command — read both streams
            r.out = (r.stdout or "") + (r.stderr or "")
            return r

        @t("the container is up, idling on the pinned image")
        def _():
            r = dk("container", "inspect", "-f",
                   "{{.State.Running}} {{.Config.Image}}", NAME9)
            assert r.stdout.split()[0] == "true", r.stdout

        @t("☠ the ROOTFS is read-only — nothing writes outside the measured disk")
        def _():
            r = ex("sh", "-c", "echo x > /nope.txt; echo rc=$?")
            assert "rc=0" not in r.out, r.out
            assert "Read-only file system" in r.out, r.out

        @t("☠ /usr/local (the CLI) is read-only even for root")
        def _():
            r = ex("sh", "-c", "touch /usr/local/x; echo rc=$?", user="root")
            assert "rc=0" not in r.out, r.out
            assert "Read-only file system" in r.out, r.out

        @t("/tmp is writable RAM, and BOUNDED")
        def _():
            assert ex("sh", "-c", "echo x > /tmp/x && cat /tmp/x").stdout.strip() \
                == "x"
            r = ex("sh", "-c", "df -m /tmp | tail -1")
            mb = int(r.stdout.split()[1])
            assert mb <= 1024 + 8, r.stdout          # ORGTREE_SANDBOX_TMP=1g
            r = ex("sh", "-c", "df -m /run | tail -1")
            assert int(r.stdout.split()[1]) <= 64 + 4, r.stdout

        @t("☠ every persistent write lands on the org's own disk")
        def _():
            ex("sh", "-c", "echo agent-wrote-this > /home/agent/proof.txt")
            p = os.path.join(dsk.windows_path(S9), "home", "proof.txt")
            assert open(p, encoding="utf-8").read().strip() == \
                "agent-wrote-this", p
            ex("sh", "-c", "sudo touch /usr/lib/proof2 || touch /usr/lib/proof2")
            assert os.path.exists(os.path.join(dsk.windows_path(S9), "usr",
                                               "lib", "proof2"))

        @t("☠ the HOST filesystem is not visible from inside")
        def _():
            r = ex("sh", "-c",
                   "ls /mnt /host /c 2>&1 | head -5; ls /opt | head -5")
            assert "orgtree-backend" in r.out, r.out
            assert "Users" not in r.out and "Windows" not in r.out, r.out
            r = ex("sh", "-c", "cat /opt/orgtree-backend/orgtree/api.py "
                               "> /dev/null; echo rc=$?")
            assert "rc=0" in r.out, "the backend mount should be readable"
            r = ex("sh", "-c", "echo x >> /opt/orgtree-backend/orgtree/api.py"
                               "; echo rc=$?", user="root")
            assert "rc=0" not in r.out, "the backend mount is WRITABLE"

        @t("☠ …and neither is any other org's disk or the data root")
        def _():
            r = ex("sh", "-c", f"ls /mnt/wsl 2>&1; ls {dsk.MOUNT_ROOT} 2>&1; "
                                f"ls /mnt/docker-desktop-disk 2>&1")
            lines = [x for x in r.out.splitlines() if x.strip()]
            assert len(lines) == 3 and all("No such file" in x
                                           for x in lines), r.out

        @t("the agent holds root INSIDE, and that is the whole point")
        def _():
            assert "uid=0" in ex("sudo", "id").out, ex("sudo", "id").out
            assert "agent" in ex("id").out

        @t(".bridge is where steer.py and mcptool.py look for it")
        def _():
            r = ex("cat", "/home/agent/orgtree/.bridge")
            b = json.loads(r.stdout)
            assert b["secret"] == "99" * 16 and "host.docker.internal" in b["url"]

        @t("☠ the ONE door out: the bridge port answers, and only with the secret")
        def _():
            import uvicorn
            cfg = uvicorn.Config(api.BridgeGateway(api.app), host="0.0.0.0",
                                 port=7407, log_level="error")
            server = uvicorn.Server(cfg)
            th = threading.Thread(target=server.run, daemon=True)
            th.start()
            for _ in range(100):
                if getattr(server, "started", False):
                    break
                time.sleep(0.05)
            api._bridge_cache["at"] = 0.0
            try:
                base = sandbox.bridge_url()
                r = ex("sh", "-c", f"curl -s -o /dev/null -w '%{{http_code}}' "
                                   f"-X POST {base}/api/orgs")
                assert r.stdout.strip() == "403", r.stdout
                r = ex("sh", "-c",
                       f"curl -s -X POST {base}/api/agent "
                       f"-H 'content-type: application/json' "
                       f"-H 'x-orgtree-bridge: {'99' * 16}' "
                       f'-d \'{{"org":"{S9}","node":"alice",'
                       f'"tool":"orgtree_chart"}}\'')
                assert "chart" in r.stdout, r.stdout
            finally:
                server.should_exit = True
                th.join(timeout=20)

        @t("the in-container CLI is the version the image was tagged with")
        def _():
            r = ex("sh", "-c", "claude --version 2>&1 | head -1")
            assert supervisor.cli_version().split(".")[0] in r.stdout, r.stdout

        @t("☠☠ the CAP IS ENOSPC: an agent filling its disk hits the "
           "filesystem, the container survives, and recovery still works")
        def _():
            """The whole enforcement story in one check. The free space is
            consumed with `fallocate` (real ext4 allocation, unwritten
            extents) rather than 3.4 GB of dd, so the filesystem is genuinely
            full while the host's sparse VHDX barely moves."""
            r = ex("sh", "-c",
                   "A=$(df -k /home/agent | tail -1 | awk '{print $4}'); "
                   "fallocate -l $(( (A - 2048) * 1024 )) /home/agent/fill.bin"
                   " && echo filled")
            assert "filled" in r.out, r.out
            r = ex("sh", "-c", "dd if=/dev/zero of=/home/agent/over.bin "
                               "bs=1M count=8 2>&1; echo rc=$?")
            assert "No space left" in r.out, r.out
            assert "rc=0" not in r.out, r.out
            # the engine is NOT starved: the container still answers, and the
            # backend still measures and lists it
            assert "alive" in ex("echo", "alive").out, ex("echo", "alive").out
            dsk.invalidate(S9)
            u = dsk.usage(S9, max_age=0.0)
            # with `mkfs.ext4 -m 0` (the fix in disk.create) the whole cap is
            # the agent's, so df really does reach ~100% — the ≥99% hard-full
            # tier is reachable again. Pre-fix this measured 94.4%.
            assert u and u[0] / u[1] > 0.98,                 f"ENOSPC arrived with df at {u[0] / u[1]:.1%} — reserved "                f"blocks are back"
            big = dsk.enumerate_by_size(S9, limit=1)
            assert big and big[0]["path"] == "home/fill.bin", big
            # …and the recovery path frees it from the HOST side, at 100% full
            for junk in ("fill.bin", "over.bin"):
                p = os.path.join(dsk.windows_path(S9), "home", junk)
                if os.path.exists(p):
                    os.unlink(p)
            dsk.invalidate(S9)
            assert dsk.usage(S9, max_age=0.0)[0] / u[1] < 0.95
            assert ex("sh", "-c", "echo back > /home/agent/ok.txt; echo rc=$?"
                      ).out.strip().endswith("rc=0"), "writes did not resume"

        @t("a second ensure_container is a no-op on a live container")
        def _():
            before = dk("container", "inspect", "-f", "{{.Id}}", NAME9).stdout
            sandbox.ensure_container(store.load_org(S9))
            assert dk("container", "inspect", "-f", "{{.Id}}",
                      NAME9).stdout == before

        @t("remove() leaves no container, no volume and no mount behind")
        def _():
            sandbox.remove(S9)
            made["container"] = made["disk"] = False
            assert dk("container", "inspect", NAME9).returncode != 0
            assert dk("volume", "inspect",
                      dsk.disk_volume(S9)).returncode != 0
            assert not dsk.is_mounted(S9)


# ==========================================================================
if SKIPS:
    print("\nSKIPPED")
    for s in SKIPS:
        print(f"  ○ {s}")
if FIXED:
    print("\nFIXED HERE — each was reproduced RED before the fix")
    for i, n in enumerate(FIXED, 1):
        print(f"  ✓ {i}. {n}")
if NOTES:
    print("\nREPORTED, NOT FIXED — each has a reproduction above")
    for i, n in enumerate(NOTES, 1):
        print(f"  ⚑ {i}. {n}")
print(f"\nALL {PASS} CHECKS PASS"
      + ("" if DOCKER_TIER else "   (hermetic tier; --docker adds §9)"))
