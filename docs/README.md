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
- [`infrastructure-tiers.md`](infrastructure-tiers.md) compares deployment
  shapes and their operational trade-offs.

## Developer references

- [`ARCHITECTURE.md`](ARCHITECTURE.md) records implementation invariants and
  traps for contributors.
- [`adding-a-provider.md`](adding-a-provider.md) is the implementation
  playbook for a new model provider.
- [`../DECISIONS.md`](../DECISIONS.md) is the normative decision register.
- [`typing-plan.md`](typing-plan.md), [`social-preview.md`](social-preview.md),
  and [`message-visibility-suite.md`](message-visibility-suite.md) document
  maintained engineering workflows.

## Historical and exploratory records

`feature-docket.md`, `interim-docket.md`, `mobile-spec.md`,
`mailserver-spec.md`, and the files in `attic/` preserve design history,
accepted work, or exploratory reasoning. They are not the source of truth for
current runtime behavior. Verify a claim in source and then update the
appropriate current guide or decision entry.
