# Documentation map

Use the document that matches the question rather than treating every
Markdown file in this repository as current operating guidance.

## Current guides

- [`../README.md`](../README.md) is the public overview, installation path,
  and concise operational reference.
- [`setup-guide.md`](setup-guide.md) expands installation, kiosk exposure,
  provider CLI setup, and mail-hub setup.
- [`configuration.md`](configuration.md) is the authoritative reference for
  environment variables, settings, defaults, and their scope.
- [`ui-guide.md`](ui-guide.md) describes the shipped user interface and its
  controls.
- [`agent-tools.md`](agent-tools.md) is the complete agent-facing MCP tool
  catalog and its operational boundaries.
- [`cache-continuity.md`](cache-continuity.md) defines the persisted next-turn
  forecast, provider receipt evidence, and known-cold compaction policy;
  [`cache-hazards.md`](cache-hazards.md) records invalidation traps.
- [`infrastructure-tiers.md`](infrastructure-tiers.md) compares deployment
  shapes and their operational trade-offs.
- [`frozen-deployment.md`](frozen-deployment.md) covers the hardened, operator-
  controlled deployment profile and its verification boundary.
- [`autostart.md`](autostart.md) covers Windows service startup; the
  [mail-server specification](mailserver-spec.md) and
  [hub README](../hub/README.md) cover cross-organization mail.
- [`sqlite-cutover.md`](sqlite-cutover.md) is the operator runbook and
  performance reference for the canonical SQLite storage format and the
  migration off deprecated JSON.

## Developer references

- [`ARCHITECTURE.md`](ARCHITECTURE.md) records implementation invariants and
  traps for contributors.
- [`INVARIANTS.md`](INVARIANTS.md) is the canonical register of explicit
  user-stated app-state invariants — narrower than, and outranking,
  `DECISIONS.md`.
- [`antigravity-warm-and-steer.md`](antigravity-warm-and-steer.md) is the
  design — not yet built — for warming a parked Antigravity process and
  steering it mid-turn through the CLI's PreInvocation hook, with the
  probes that gate each (D-233 records what the lane can know today).
- [`adding-a-provider.md`](adding-a-provider.md) is the implementation
  playbook for a new model provider.
- [`../DECISIONS.md`](../DECISIONS.md) is the normative decision register.
- [`typing-plan.md`](typing-plan.md), [`social-preview.md`](social-preview.md),
  and [`message-visibility-suite.md`](message-visibility-suite.md) document
  maintained engineering workflows.
- [`test-baseline.md`](test-baseline.md) is how to tell YOUR breakage from
  the suites that were already failing. `main` is not green, so "expect
  green" is the wrong bar and the right one is parity against a baseline you
  measured yourself, at your own tip. Read it before you quote a failure
  count at anyone: three separate things silently change the number — where
  you ran it, whether your `node_modules` is a symlink, and which suites the
  runner skipped.

## Historical and exploratory records

[`history/PLAN.md`](history/PLAN.md),
[`history/feature-docket.md`](history/feature-docket.md),
[`history/interim-docket.md`](history/interim-docket.md), `mobile-spec.md`,
and the files in `attic/` preserve design history,
accepted work, or exploratory reasoning. They are not the source of truth for
current runtime behavior. Verify a claim in source and then update the
appropriate current guide or decision entry.
