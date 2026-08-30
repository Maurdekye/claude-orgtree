"""Verify that this running install matches frozen/approved-install.json."""

from __future__ import annotations

import argparse
import json
import os
import sys


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from orgtree import deployment, frozen_install  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="attest the active frozen orgtree installation")
    parser.add_argument("--json", action="store_true",
                        help="emit the complete machine-readable report")
    parser.add_argument("--verbose", action="store_true",
                        help="show passing checks as well as failures")
    parser.add_argument("--skip-containers", action="store_true",
                        help="diagnostic only: omit live image checks; cannot "
                             "prove the complete running installation")
    parser.add_argument("--build-commands", action="store_true",
                        help="print exact commands for the approved frozen images")
    args = parser.parse_args()

    try:
        policy = deployment.current_policy()
        if args.build_commands:
            if policy.name != "frozen":
                print("REFUSING: build commands are available only while the "
                      "authoritative deployment profile is frozen.", file=sys.stderr)
                return 2
            print("\n".join(frozen_install.build_commands()))
            return 0
        report = frozen_install.verify_approved_install(
            policy=policy, include_containers=not args.skip_containers)
    except deployment.DeploymentConfigError as e:
        print(f"FROZEN INSTALLATION REFUSED\n{e}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    else:
        print(frozen_install.format_report(report, verbose=args.verbose))
        if args.skip_containers:
            print("WARNING: container checks were skipped; this is not a "
                  "complete running-installation attestation.")
    return 0 if report.ok and not args.skip_containers else 1


if __name__ == "__main__":
    raise SystemExit(main())
