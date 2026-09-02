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

## Developer references

- [`ARCHITECTURE.md`](ARCHITECTURE.md) records implementation invariants and
  traps for contributors.
- [`INVARIANTS.md`](INVARIANTS.md) is the canonical register of explicit
  user-stated app-state invariants — narrower than, and outranking,
  `DECISIONS.md`.
- [`adding-a-provider.md`](adding-a-provider.md) is the implementation
  playbook for a new model provider.
- [`../DECISIONS.md`](../DECISIONS.md) is the normative decision register.
- [`typing-plan.md`](typing-plan.md), [`social-preview.md`](social-preview.md),
  and [`message-visibility-suite.md`](message-visibility-suite.md) document
  maintained engineering workflows.

## Historical and exploratory records

[`history/PLAN.md`](history/PLAN.md),
[`history/feature-docket.md`](history/feature-docket.md),
[`history/interim-docket.md`](history/interim-docket.md), `mobile-spec.md`,
and the files in `attic/` preserve design history,
accepted work, or exploratory reasoning. They are not the source of truth for
current runtime behavior. Verify a claim in source and then update the
appropriate current guide or decision entry.
