"""The Claude Code CLI pin, and the Fable 5.1 migration that needed it.

WHAT THIS IS ABOUT. The fable tier's default model id moved to
`claude-fable-5-1`, which exists only in CLI **2.1.257 and later** — measured
by grepping each published build's native binary for the literal id (2.1.220
absent, 2.1.251 absent, 2.1.252 absent, 2.1.257 present, 2.1.258 present;
2.1.253–256 were never published, so the floor is exact). The pin moved to
2.1.258.

THE MIGRATION HAZARD THIS SUITE EXISTS FOR. Two things move on different
clocks. The org doc's fable id migrates the moment the new CODE loads; the CLI
pin migrates only when a DEPLOY runs. Between them sits a machine whose orgs
ask for 5.1 and whose CLI has never heard of it — and 2.1.220 does not refuse
an unknown `--model`, it forwards it (measured against a dead endpoint:
`claude-fable-5-1` and a deliberately bogus id behave identically). There is no
loud local failure to catch, so every check below is about orgtree noticing on
its own.

    §1  the three numbers, and why they are three
    §2  the downgrade itself
    §3  the call sites actually READ it (a gate nobody reads is not a gate)
    §4  existing orgs migrate — and customised ones do not
    §5  the update scripts read the pin from the code, not from a literal
    §6  DRIFT GUARD: the frozen lane agrees with the pin, everywhere
    §7  DRIFT GUARD: the frontend's version menu mirrors the ledger's
    §8  controls — what would make the above vacuous

    python backend/tests/test_cli_pin.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="orgtree-clipin-")
os.environ["ORGTREE_DATA"] = _TMP
os.makedirs(_TMP, exist_ok=True)
with open(os.path.join(_TMP, "defaults.json"), "w", encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from orgtree import clipin, ledger, store                    # noqa: E402
from orgtree import supervisor as sup                        # noqa: E402
from orgtree.ledger import USER, Org                         # noqa: E402

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))

FAILED: list[str] = []
PASSED = 0


def check(label, fn) -> None:
    global PASSED
    try:
        fn()
    except Exception as e:                                     # noqa: BLE001
        FAILED.append(f"{label}\n      {type(e).__name__}: {e}")
        print(f"  x {label}")
    else:
        PASSED += 1
        print(f"  ok {label}")


def read(rel: str) -> str:
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


class AtVersion:
    """Run a block as though the resolved CLI reported `ver`.

    Patches `supervisor.cli_version`, which is the ONE reader every gate goes
    through — patching a gate directly would test the test.
    """

    def __init__(self, ver: str) -> None:
        self.ver = ver

    def __enter__(self):
        self._real = sup.cli_version
        sup.cli_version = lambda: self.ver     # type: ignore[assignment]
        return self

    def __exit__(self, *a) -> None:
        sup.cli_version = self._real           # type: ignore[assignment]


_n = 0


def fable_org() -> tuple[Org, str]:
    """A saved org with one live fable node. Saved because `_build_cmd` reads
    it back through the store."""
    global _n
    _n += 1
    o = Org.create(f"clipin{_n}")
    o.d["max_top_grant"] = 200
    nid = o.hire(USER, None, "fable", 20, "f")["node"]
    store.save_org(o)
    return o, nid


# ------------------------------------------------ §1 the three numbers
print("\n§1  the three numbers, and why they are three")


def floor_is_exact() -> None:
    """The measured boundary, both sides. 2.1.256 stands in for "anything
    below": it was never published, and that is the point — nothing between
    252 and 257 exists, so the floor cannot be off by a published build."""
    assert clipin.knows_fable_5_1("2.1.257") is True
    assert clipin.knows_fable_5_1("2.1.258") is True
    assert clipin.knows_fable_5_1("2.1.256") is False
    assert clipin.knows_fable_5_1("2.1.252") is False
    assert clipin.knows_fable_5_1("2.1.251") is False
    assert clipin.knows_fable_5_1("2.1.220") is False


def floor_reads_a_real_version_banner() -> None:
    """The CLI prints `2.1.258 (Claude Code)`, not a bare number. A floor that
    only worked on the trimmed form would pass every check here and fail on
    every real machine."""
    assert clipin.knows_fable_5_1("2.1.258 (Claude Code)") is True
    assert clipin.knows_fable_5_1("2.1.220 (Claude Code)") is False


def unreadable_version_fails_CLOSED() -> None:
    """⚠ THE OPPOSITE RULE TO `cli_capable`, on purpose. Ignorance about
    CAPABILITY must not degrade a turn that would have worked, so that one
    fails open. Ignorance HERE costs one model version and the fallback is a
    working model, so this one fails closed. If both ever read the same way,
    one of them is wrong."""
    assert clipin.knows_fable_5_1("unknown") is False
    assert clipin.knows_fable_5_1("") is False
    with AtVersion("unknown"):
        assert sup.cli_knows_fable_5_1() is False
        assert sup.cli_capable() is True         # the other rule, still open


def the_pin_clears_the_floor() -> None:
    """A pin below the floor would ship a deploy that installs a CLI which
    still cannot say the tier's own default id."""
    assert clipin.ver_tuple(clipin.PIN) >= clipin.FABLE_5_1_MIN, clipin.PIN
    assert clipin.knows_fable_5_1(clipin.PIN) is True


def capability_floor_did_NOT_move() -> None:
    """The whole compatibility story. `_CLI_MIN` is what decides whether a turn
    is DEGRADED (no --effort, no headless tool hooks). Dragging it up to the
    new pin would declare every machine that has not redeployed yet incapable —
    the opposite of a migration. It must stay strictly below the fable floor,
    and 2.1.220 (the pin this migration is FROM) must still be capable."""
    assert sup._CLI_MIN < clipin.FABLE_5_1_MIN, (sup._CLI_MIN,
                                                 clipin.FABLE_5_1_MIN)
    with AtVersion("2.1.220"):
        assert sup.cli_capable() is True
        assert sup.cli_diagnosis() is None       # not even a complaint
        assert sup.cli_knows_fable_5_1() is False


check("the fable floor is exact on both sides", floor_is_exact)
check("…and reads the CLI's real `X.Y.Z (Claude Code)` banner",
      floor_reads_a_real_version_banner)
check("an unreadable version fails CLOSED here and OPEN for capability",
      unreadable_version_fails_CLOSED)
check("the shipped PIN clears the floor", the_pin_clears_the_floor)
check("the CAPABILITY floor did not move — the old pin is still capable",
      capability_floor_did_NOT_move)


# --------------------------------------------------- §2 the downgrade
print("\n§2  the downgrade itself")


def new_cli_gets_5_1() -> None:
    o, nid = fable_org()
    with AtVersion(clipin.PIN):
        assert sup.claude_model_for(o, nid) == clipin.FABLE_5_1


def old_cli_gets_5_0() -> None:
    o, nid = fable_org()
    with AtVersion("2.1.220"):
        assert sup.claude_model_for(o, nid) == clipin.FABLE_5


def a_chosen_version_still_wins() -> None:
    """A node pinned to Fable 5 in the ⚙ gear gets 5.0 on ANY CLI — the
    downgrade must not become the only path to 5.0, or the gear stops working
    the day the fleet upgrades."""
    o, nid = fable_org()
    o.set_scope(USER, nid, model_version="5")
    store.save_org(o)
    for ver in (clipin.PIN, "2.1.220"):
        with AtVersion(ver):
            assert sup.claude_model_for(o, nid) == clipin.FABLE_5, ver


def other_tiers_are_untouched() -> None:
    """It rewrites ONE id. A general "is this model known" filter is exactly
    what this must not grow into — orgtree has no per-version registry it could
    keep honest."""
    global _n
    _n += 1
    o = Org.create(f"clipin{_n}")
    o.d["max_top_grant"] = 200
    ids = {}
    for tier in ("opus", "sonnet", "haiku"):
        nid = o.hire(USER, None, tier, 20, tier)["node"]
        ids[tier] = nid
    store.save_org(o)
    for tier, nid in ids.items():
        want = o.model_for(nid)
        for ver in (clipin.PIN, "2.1.220", "unknown"):
            with AtVersion(ver):
                assert sup.claude_model_for(o, nid) == want, (tier, ver)


check("a current CLI is handed Fable 5.1", new_cli_gets_5_1)
check("an old-pin CLI is handed Fable 5 instead", old_cli_gets_5_0)
check("a node that CHOSE Fable 5 keeps it on every CLI",
      a_chosen_version_still_wins)
check("no other tier's model id is ever rewritten", other_tiers_are_untouched)


# ------------------------------------------- §3 the call sites read it
print("\n§3  the call sites actually READ it")


def model_in_argv(o: Org, nid: str) -> str:
    argv = sup._build_cmd(store.load_org(o.d["slug"]), nid, write_ident=False)
    return argv[argv.index("--model") + 1]


def build_cmd_honours_the_floor() -> None:
    """⚠ THE REAL SPAWN ARGV, not the predicate. A gate that is computed and
    never read is the abstention shape this repo keeps getting caught by: make
    `_build_cmd` call `org.model_for` again and §2 still passes while every
    old-pin machine sends a model id its CLI cannot name."""
    o, nid = fable_org()
    with AtVersion(clipin.PIN):
        assert model_in_argv(o, nid) == clipin.FABLE_5_1
    with AtVersion("2.1.220"):
        assert model_in_argv(o, nid) == clipin.FABLE_5


def the_cache_namespace_moves_with_it() -> None:
    """A namespace is only worth anything if it moves when the REQUEST moves.
    On an old pin a 5.1 node's turns really are served by 5.0, and the day that
    machine redeploys the model genuinely changes — which is a cache-namespace
    break and has to be visible as one."""
    o, nid = fable_org()
    with AtVersion("2.1.220"):
        old = sup._cache_snapshot(o, nid, include_history=False)
    with AtVersion(clipin.PIN):
        new = sup._cache_snapshot(o, nid, include_history=False)
    # the namespace keys are spread at the top level of the snapshot
    assert old["model"] == clipin.FABLE_5, old["model"]
    assert new["model"] == clipin.FABLE_5_1, new["model"]
    assert old["fingerprint"] != new["fingerprint"]


def resolution_reports_both_facts() -> None:
    """The downgrade is SILENT by design — a turn that quietly ran on 5.0
    leaves no other trace a user can see. /api/host carries the answer."""
    with AtVersion("2.1.220"):
        r = sup.cli_resolution()
    assert r["fable_5_1"] is False, r
    assert r["pin_version"] == clipin.PIN, r
    with AtVersion(clipin.PIN):
        assert sup.cli_resolution()["fable_5_1"] is True


check("_build_cmd's real --model argv follows the floor",
      build_cmd_honours_the_floor)
check("the cache namespace records the model that ACTUALLY goes out",
      the_cache_namespace_moves_with_it)
check("cli_resolution reports the pin and the 5.1 answer",
      resolution_reports_both_facts)


# ------------------------------------------ §4 existing orgs migrate
print("\n§4  existing orgs migrate — and customised ones do not")


def fresh_org_is_on_5_1() -> None:
    global _n
    _n += 1
    o = Org.create(f"clipin{_n}")
    assert o.d["models"]["fable"] == clipin.FABLE_5_1


def an_existing_org_moves_forward() -> None:
    """⚠ THE ONE THAT WOULD SHIP TO NOBODY. `Org.create` COPIES the module
    table into the doc and the load hook is `setdefault`/add-only, so changing
    `MODELS["fable"]` reaches no org that already exists — the key is present,
    5.0 stays there forever, and the only evidence is an org card still reading
    the old id."""
    global _n
    _n += 1
    o = Org.create(f"clipin{_n}")
    o.d["models"]["fable"] = clipin.FABLE_5          # an org from before
    nid = o.hire(USER, None, "fable", 20, "f")["node"]
    store.save_org(o)
    back = store.load_org(o.d["slug"])
    assert back.d["models"]["fable"] == clipin.FABLE_5_1, back.d["models"]
    assert back.model_for(nid) == clipin.FABLE_5_1


def a_customised_id_is_left_alone() -> None:
    """Only the OLD SHIPPED DEFAULT migrates — the same discipline as the
    sonnet 3→2 price move. A fixed id in `models` is how an operator holds a
    tier still, and a deploy that overwrote it would be taking that away."""
    global _n
    _n += 1
    o = Org.create(f"clipin{_n}")
    o.d["models"]["fable"] = "claude-fable-5-custom-pin"
    store.save_org(o)
    back = store.load_org(o.d["slug"])
    assert back.d["models"]["fable"] == "claude-fable-5-custom-pin"


def the_seat_did_not_move_with_the_model() -> None:
    """A model VERSION is a subcategory inside a price band; it never changes
    the seat. Fable's price did not change, so neither may its seat."""
    assert ledger.TIERS["fable"] == 10
    global _n
    _n += 1
    o = Org.create(f"clipin{_n}")
    assert o.d["tiers"]["fable"] == 10


def five_point_zero_stays_selectable() -> None:
    v = ledger.MODEL_VERSIONS["fable"]
    assert v["5.1"] == clipin.FABLE_5_1 and v["5"] == clipin.FABLE_5, v
    assert next(iter(v)) == "5.1", "the tier default must be listed first"


check("a fresh org is created on Fable 5.1", fresh_org_is_on_5_1)
check("an org that predates the change is migrated on load",
      an_existing_org_moves_forward)
check("…but an operator's own fable id is NOT overwritten",
      a_customised_id_is_left_alone)
check("the seat cost did not move with the model",
      the_seat_did_not_move_with_the_model)
check("Fable 5 is still selectable, and 5.1 is listed first",
      five_point_zero_stays_selectable)


# ----------------------------------- §5 the update scripts read the pin
print("\n§5  the update scripts read the pin from the code")


def clipin_imports_nothing() -> None:
    """The whole reason it is its own module. `update.ps1`/`update.sh` import
    it at a point in the deploy where nothing else is known to be healthy, so
    it must not be able to fail for a reason unrelated to the pin. Re-imported
    in a clean subprocess because this process has already loaded the world."""
    import subprocess
    out = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, sys.argv[1]);"
         " import orgtree.clipin;"
         " print(sorted(m for m in sys.modules if m.startswith('orgtree')))",
         os.path.join(ROOT, "backend")],
        capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "['orgtree', 'orgtree.clipin']", out.stdout


def pin_is_an_exact_version() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", clipin.PIN), clipin.PIN
    assert clipin.PACKAGE == "@anthropic-ai/claude-code"


def both_scripts_read_it_from_clipin() -> None:
    """⚠ THE DRIFT THIS PREVENTS: a version written down twice is a machine
    that reports one number and runs another.

    The guard is on the INSTALL SPEC, not on the whole file: both scripts are
    thick with prose naming versions that were measured once (`2.1.31` on a
    machine whose runtime was the `2.1.220` pin, `esbuild 0.25.12`), and a
    blanket "no version literals" rule would forbid recording evidence. What
    must never be a literal is the thing npm is actually handed."""
    for rel in ("update.ps1", "update.sh"):
        src = read(rel)
        assert "clipin.PIN" in src, rel
        specs = re.findall(r"@anthropic-ai/claude-code@(\S+)", src)
        assert specs, rel
        for spec in specs:
            assert spec.startswith("$"), (rel, spec)


def both_scripts_pin_exactly_and_never_downgrade() -> None:
    """Two properties the migration rests on, read out of the scripts because
    neither can be exercised without a deploy: `--save-exact` (the fleet's
    existing installs carry a CARET range, so a re-install drifts with the
    registry — the opposite of a pin), and the floor comparison that leaves a
    NEWER CLI alone rather than rolling an operator backwards."""
    for rel in ("update.ps1", "update.sh"):
        src = read(rel)
        assert "--save-exact" in src, rel
        assert "NEWER" in src, rel


def the_install_happens_while_nothing_is_running() -> None:
    """On Windows a running process holds its own image open, so npm cannot
    overwrite bin\\claude.exe while a turn is in flight. The pin step must sit
    between the stop and the start — checked by ORDER in the file, which is the
    only thing that makes it true."""
    for rel, stop, start in (
            ("update.ps1", "stopping old backend", "Start-Process -FilePath $py"),
            ("update.sh", "stopping old backend", "nohup \"$PY\"")):
        src = read(rel)
        i_stop, i_pin, i_start = (src.index(stop), src.index("== claude cli =="),
                                  src.index(start))
        assert i_stop < i_pin < i_start, (rel, i_stop, i_pin, i_start)


check("clipin imports nothing but itself", clipin_imports_nothing)
check("PIN is an exact version of the right package", pin_is_an_exact_version)
check("neither update script carries its own version literal",
      both_scripts_read_it_from_clipin)
check("both pin exactly and refuse to roll a newer CLI backwards",
      both_scripts_pin_exactly_and_never_downgrade)
check("the pin install sits between the stop and the start",
      the_install_happens_while_nothing_is_running)


# ------------------------------------------- §6 DRIFT GUARD: frozen lane
print("\n§6  DRIFT GUARD — the frozen lane agrees with the pin")


def frozen_manifest_matches_the_pin() -> None:
    import json
    m = json.loads(read("frozen/approved-install.json"))
    claude = [p for p in m["providers"] if p["id"] == "claude"][0]
    assert claude["version"] == clipin.PIN, claude["version"]
    assert claude["package"] == clipin.PACKAGE
    sandbox = [c for c in m["containers"] if c["id"] == "sandbox"][0]
    assert (sandbox["labels"]["org.opencontainers.image.version"]
            == clipin.PIN), sandbox["labels"]


def frozen_lock_and_dockerfile_match() -> None:
    """Four files have to say the same version or the frozen image builds one
    CLI and attests another. D-208's rule generalised: when a profile pins an
    artifact, everything naming that artifact moves with it."""
    import json
    pkg = json.loads(read("frozen/sandbox-provider/package.json"))
    assert pkg["dependencies"][clipin.PACKAGE] == clipin.PIN
    lock = json.loads(read("frozen/sandbox-provider/package-lock.json"))
    root = lock["packages"][""]["dependencies"][clipin.PACKAGE]
    assert root == clipin.PIN, root
    entry = lock["packages"][f"node_modules/{clipin.PACKAGE}"]
    assert entry["version"] == clipin.PIN, entry["version"]
    assert (f'org.opencontainers.image.version="{clipin.PIN}"'
            in read("frozen/sandbox.Dockerfile"))


def the_manifest_integrity_is_the_lock_integrity() -> None:
    """The manifest's `integrity` is what a verifier compares an installed
    tree against. Copied by hand once; pinned here so the next bump cannot move
    the version and leave the old hash behind — which would attest a package
    nobody installed."""
    import json
    m = json.loads(read("frozen/approved-install.json"))
    claude = [p for p in m["providers"] if p["id"] == "claude"][0]
    lock = json.loads(read("frozen/sandbox-provider/package-lock.json"))
    entry = lock["packages"][f"node_modules/{clipin.PACKAGE}"]
    assert claude["integrity"] == entry["integrity"], (claude["integrity"],
                                                       entry["integrity"])


def the_manifests_own_digest_moved_with_it() -> None:
    """⚠ THE INTERLOCK THAT CAUGHT THIS CHANGE. `frozen_install.py` pins the
    sha256 of `approved-install.json` ITSELF, independently, so a manifest and
    all of its referenced files cannot be quietly edited into a new approval —
    the constant in code has to move too, in the same commit.

    Bumping the pin edits the manifest, so it trips this by construction. It
    was NOT caught by reading the diff or by grepping for the obvious names; it
    surfaced as `test_sandbox` dying with `DeploymentConfigError
    [MANIFEST_DIGEST]` in a clean-tree run, three steps from the file I had
    changed. Hence a check that names it directly."""
    import hashlib
    from orgtree import frozen_install
    with open(os.path.join(ROOT, "frozen/approved-install.json"), "rb") as fh:
        raw = fh.read()
    got = hashlib.sha256(raw).hexdigest()
    lf = hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()
    assert frozen_install.APPROVED_MANIFEST_SHA256 in (got, lf), (
        f"APPROVED_MANIFEST_SHA256 is stale: manifest hashes to {lf}")


def approved_file_hashes_are_current() -> None:
    """`frozen/**` is `text eol=lf` in .gitattributes and the manifest hashes
    LF bytes, so this compares against the normalised form: a Windows checkout
    of a file whose eol rule was added later can still hold CRLF, and that is a
    stale checkout rather than a bad hash."""
    import hashlib
    import json
    m = json.loads(read("frozen/approved-install.json"))
    for rel, expected in sorted(m["files"].items()):
        with open(os.path.join(ROOT, rel), "rb") as fh:
            raw = fh.read()
        got = hashlib.sha256(raw).hexdigest()
        lf = hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()
        assert expected in (got, lf), (rel, expected, got, lf)


check("the frozen manifest names the pinned version", frozen_manifest_matches_the_pin)
check("package.json, the lock and the Dockerfile label all agree",
      frozen_lock_and_dockerfile_match)
check("the manifest's integrity is the lock's integrity",
      the_manifest_integrity_is_the_lock_integrity)
check("frozen_install's independently pinned manifest digest moved too",
      the_manifests_own_digest_moved_with_it)
check("every approved file hash still matches its file",
      approved_file_hashes_are_current)


# ---------------------------------------- §7 DRIFT GUARD: the ⚙ menu
print("\n§7  DRIFT GUARD — the frontend's version menu mirrors the ledger")


def frontend_mirrors_model_versions() -> None:
    """`shared.ts` hand-mirrors `ledger.MODEL_VERSIONS`, and the gear renders
    `versions[0]` as "latest (X)" — so both the SET and the ORDER matter. A
    tier added to the ledger and forgotten here offers no choice in the UI; a
    wrong first entry labels the tier default as the older version."""
    src = read("frontend/src/canvas/shared.ts")
    m = re.search(r"MODEL_VERSIONS:\s*Record<string,\s*string\[\]>\s*=\s*"
                  r"\{(.*?)\}", src, re.S)
    assert m, "MODEL_VERSIONS not found in shared.ts"
    body = m.group(1)
    for tier, table in ledger.MODEL_VERSIONS.items():
        want = ", ".join(f"'{k}'" for k in table)
        assert f"{tier}: [{want}]" in body, (tier, want, body.strip())


check("shared.ts lists the same versions, in the same order",
      frontend_mirrors_model_versions)


# ------------------------------------------------------ §8 controls
print("\n§8  controls — what would make the above vacuous")


def the_two_ids_are_different_strings() -> None:
    """Almost every check above is an equality against one of these two. If
    they ever collapsed to the same string, the whole suite would pass while
    doing nothing."""
    assert clipin.FABLE_5 != clipin.FABLE_5_1
    assert clipin.FABLE_5 and clipin.FABLE_5_1


def AtVersion_actually_drives_the_gates() -> None:
    """§1–§3 are all written through this fixture. If patching
    `supervisor.cli_version` stopped reaching the gates, every one of them
    would pass against whatever this machine happens to run."""
    with AtVersion("2.1.220"):
        assert sup.cli_version() == "2.1.220"
        low = sup.cli_knows_fable_5_1()
    with AtVersion("2.1.258"):
        high = sup.cli_knows_fable_5_1()
    assert (low, high) == (False, True)
    assert sup.cli_version is not None      # and it was put back


def build_cmd_really_emits_a_model() -> None:
    """§3 reads `argv[index('--model') + 1]`. If the flag ever stopped being
    emitted, `.index` would raise inside the check and read as a failure — but
    only while something still calls it, so the plain fact is pinned here."""
    o, nid = fable_org()
    argv = sup._build_cmd(store.load_org(o.d["slug"]), nid, write_ident=False)
    assert "--model" in argv, argv


def the_frozen_files_are_not_empty() -> None:
    """§6 asserts containment and equality against parsed JSON. Empty or
    unparseable files would fail loudly — but an empty `files` map would make
    the hash loop iterate zero times and pass."""
    import json
    m = json.loads(read("frozen/approved-install.json"))
    assert len(m["files"]) >= 8, len(m["files"])
    assert len(m["providers"]) >= 1


check("Fable 5 and Fable 5.1 are distinct, non-empty ids",
      the_two_ids_are_different_strings)
check("the version fixture really drives the gates",
      AtVersion_actually_drives_the_gates)
check("_build_cmd really emits a --model flag", build_cmd_really_emits_a_model)
check("the frozen manifest is non-trivial", the_frozen_files_are_not_empty)


# ------------------------------------------------------------------ summary
print(f"\n{'=' * 60}")
if FAILED:
    print(f"FAILED {len(FAILED)} / {PASSED + len(FAILED)}")
    for f in FAILED:
        print(f"  x {f}")
    sys.exit(1)
print(f"PASSED {PASSED}/{PASSED}")
