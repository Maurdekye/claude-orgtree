"""The tier-discovery API surface carries the tool declaration, three-state.

    python backend/tests/test_tier_discovery_capability.py

`api._tier_discovery_payload` is the allowlisted projection an AGENT reads
when it asks what it may hire. Its default value arm admits `str` or `None`
and rejects everything else, so a boolean field needs its own branch —
naming `tools` in the allowlist alone would have turned every OpenRouter tier
into "provider discovery returned a malformed tier" and taken the whole
document down with it. That is the failure this file exists to hold shut.

⚠ EVERY VALUE HERE IS A CATALOG DECLARATION, NOT AN OBSERVATION. Nothing in
this file runs a turn or a tool call against any model.

Hermetic: `openrouter._http_get` is a scripted stand-in, the data root is a
throwaway directory, and the other providers are pinned at paths that do not
exist so rendering the whole providers document probes nothing.
"""

import json
import os
import shutil
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
DATA = tempfile.mkdtemp(prefix="orgtree-tier-discovery-")
os.environ["ORGTREE_DATA"] = DATA
os.environ.pop("ORGTREE_WARM", None)
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')
os.environ["ORGTREE_CODEX"] = os.path.join(DATA, "nowhere", "codex.exe")
os.environ["CODEX_HOME"] = os.path.join(DATA, "chome")
os.environ["ORGTREE_ANTIGRAVITY"] = os.path.join(DATA, "nowhere", "agy.exe")

from orgtree import api, openrouter as orr, store        # noqa: E402

LIVE = os.path.normcase(os.path.realpath(os.path.expanduser("~/orgtree")))
assert os.path.normcase(os.path.realpath(store.DATA_ROOT)) != LIVE, store.DATA_ROOT

PASS = 0


def check(label, fn):
    global PASS
    fn()
    PASS += 1
    print(f"  ok {PASS:2d}  {label}")


def eq(got, want, what):
    if got != want:
        raise AssertionError(f"{what}: got {got!r}, wanted {want!r}")


def raises(fn, needle, what):
    try:
        fn()
    except Exception as e:                                   # noqa: BLE001
        assert needle in str(e), f"{what}: wrong error {e!r}"
        return
    raise AssertionError(f"{what}: no error raised")


# ── the scripted catalog: one model per declaration state ───────────────
CATALOG = {"data": [
    {"id": "vendor/declares-tools", "name": "Vendor: Declares Tools",
     "context_length": 200000, "pricing": {"prompt": "0.000003",
                                           "completion": "0.000015"},
     "supported_parameters": ["tools", "temperature"]},
    {"id": "vendor/declares-none", "name": "Vendor: Declares None",
     "context_length": 8192, "pricing": {"prompt": "0.0000001",
                                         "completion": "0.0000002"},
     "supported_parameters": ["temperature"]},
    # no `supported_parameters` at all — the catalog declared nothing readable
    {"id": "vendor/silent", "name": "Vendor: Silent",
     "context_length": 32768, "pricing": {"prompt": "0.000001",
                                          "completion": "0.000002"}},
]}
KEY_DOC = {"data": {"label": "probe", "limit": None, "usage": 0}}


def fake_get(url, headers):
    return 200, json.dumps(CATALOG if "/models" in url else KEY_DOC).encode()


orr._http_get = fake_get
orr.set_key("sk-or-v1-DISCOVERYFAKEKEY-00000000000000000000")
orr.refresh_catalog()
for mid in ("vendor/declares-tools", "vendor/declares-none", "vendor/silent"):
    orr.add_favorite(mid)


def tiers_by_model():
    doc = api._tier_discovery_payload()
    entry = [p for p in doc["providers"] if p["id"] == orr.PROVIDER_ID][0]
    return {t["tier"]: t for t in entry["tiers"]}


def main():
    print("tier discovery: the tool declaration crosses the API boundary")

    rows = tiers_by_model()

    def three_states_survive():
        eq(rows[orr.tier_id("vendor/declares-tools")]["tools"], True,
           "declared support")
        eq(rows[orr.tier_id("vendor/declares-none")]["tools"], False,
           "declared no support")
        eq(rows[orr.tier_id("vendor/silent")]["tools"], None,
           "declared nothing readable")
        # ⚠ UNKNOWN MUST SURVIVE AS A DISTINCT VALUE. `None` and `False` are
        # different answers here, and a projection that collapsed them would
        # tell an agent a model was declared tool-less when nothing said so.
        assert (rows[orr.tier_id("vendor/silent")]["tools"]
                is not rows[orr.tier_id("vendor/declares-none")]["tools"])
    check("all three declaration states cross the allowlist intact",
          three_states_survive)

    def unknown_is_present_not_dropped():
        # An allowlist that simply omitted an unset field would make unknown
        # indistinguishable from a tier the projection forgot.
        eq("tools" in rows[orr.tier_id("vendor/silent")], True,
           "unknown is a PRESENT null, not an absent key")
        # positive control: a field this projection genuinely does carry
        eq(rows[orr.tier_id("vendor/silent")]["context"], 32768, "context")
    check("unknown is served as a present null rather than an absent key",
          unknown_is_present_not_dropped)

    def malformed_is_refused():
        # The value arm must REJECT anything that is not True/False/None.
        # Identity, not truthiness: `1 == True`, so a `value in (True, False)`
        # test would let an integer through as a declaration.
        real = api._providers_payload

        def bad(value):
            def patched():
                doc = real()
                for provider in doc["providers"]:
                    if provider["id"] == orr.PROVIDER_ID:
                        provider["tiers"][0]["tools"] = value
                return doc
            return patched

        try:
            for value in (1, 0, "true", "", [], {}, 1.0):
                api._providers_payload = bad(value)
                raises(api._tier_discovery_payload, "malformed tier",
                       f"tools={value!r}")
            # …and the three legitimate values still pass through the same arm
            for value in (True, False, None):
                api._providers_payload = bad(value)
                api._tier_discovery_payload()
        finally:
            api._providers_payload = real
    check("a non-boolean tools value is refused as a malformed tier, while "
          "True/False/None pass", malformed_is_refused)

    def other_providers_unaffected():
        # C4: the discovery document's shape for every other lane is
        # unchanged — this unit adds one optional field on one provider.
        doc = api._tier_discovery_payload()
        for provider in doc["providers"]:
            if provider["id"] == orr.PROVIDER_ID:
                continue
            for row in provider["tiers"]:
                eq("tools" in row, False,
                   f"{provider['id']} tier gained a tools key")
        assert doc["advisory"]
    check("no other provider's tier rows changed shape", other_providers_unaffected)

    print(f"\nALL {PASS} CHECKS PASS")


if __name__ == "__main__":
    try:
        main()
    finally:
        shutil.rmtree(DATA, ignore_errors=True)
