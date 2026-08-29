# Documentation guide

This page routes readers to the current documentation. The [README](../README.md)
is the short project overview and install entry point; the documents below are
the detailed references.

## Use orgtree

- [Setup guide](setup-guide.md) — install, start the service, connect provider
  CLIs, and create the first organization.
- [UI guide](ui-guide.md) — canvas, inbox, settings, card controls, and kiosk
  behavior.
- [Agent tools](agent-tools.md) — the complete MCP tool catalog available to
  agents.
- [Configuration reference](configuration.md) — environment variables, provider
  behavior, org settings, and defaults.
- [Infrastructure tiers](infrastructure-tiers.md) — operational sizing and
  deployment choices.
- [Autostart](autostart.md) — Windows service startup.
- [Mail server specification](mailserver-spec.md) and [hub README](../hub/README.md)
  — cross-organization and external mail.

## Develop orgtree

- [Architecture](ARCHITECTURE.md) — backend, frontend, persistence, and
  operational design.
- [Adding a provider](adding-a-provider.md) — provider integration contracts and
  the checks that keep provider behavior aligned.
- The README's [development section](../README.md#development) — the test
  runner, test tiers, and type-checking commands.
- [Message visibility suite](message-visibility-suite.md) — the specialized
  regression suite for mail and turn visibility.

## Historical material

The planning and decision record is retained for context, not as current
product reference. It will live under `docs/history/`; until then see
[PLAN](../PLAN.md), [feature docket](feature-docket.md), and
[interim docket](interim-docket.md). For current behavior, prefer the use and
development documents above.
