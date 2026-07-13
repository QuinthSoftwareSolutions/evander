# Documentation Map

This directory contains current operator and developer references, focused subsystem guides,
generated or synchronized manual sources, historical design records, and managed upstream
documentation. The classifications below are part of the maintenance contract.

## Authoritative Entry Points

- [`../README.md`](../README.md): project overview and first-run path
- [`operations.md`](operations.md): host installation, service operation, and recovery
- [`architecture.md`](architecture.md): current control-plane architecture and trust boundaries
- [`configuration-reference.md`](configuration-reference.md): host, directory, provider, and
  service configuration
- [`admin-ui-user-manual.md`](admin-ui-user-manual.md): supported Admin Web workflows
- [`evander-user-manual.md`](evander-user-manual.md): complete operator manual
- [`addons.md`](addons.md): add-on lifecycle, bundled add-ons, and Team action reference
- [`execution-backends.md`](execution-backends.md): backend selection and shared contracts
- [`container-worker-backend.md`](container-worker-backend.md): worker VM/container deployment
- [`git-remotes.md`](git-remotes.md): managed repository and restricted Git flow
- [`live-smoke-tests.md`](live-smoke-tests.md): executable live verification procedures

## Focused References

- [`config-migrations.md`](config-migrations.md): configuration migration history and procedures
- [`conformance-reports.md`](conformance-reports.md): conformance report schema and API
- [`trigger-conformance.md`](trigger-conformance.md): trigger and workflow conformance procedures
- [`libvirt-vm-template-operations.md`](libvirt-vm-template-operations.md): libvirt template and
  storage operations
- [`teams-meeting-media-bridge.md`](teams-meeting-media-bridge.md): Teams meeting media bridge
- [`workflow-run-inspector-demo.md`](workflow-run-inspector-demo.md): workflow run-inspector demo
- [`team-addon-wake-decision-tree.md`](team-addon-wake-decision-tree.md): Team wake scheduling and
  dispatch decisions

## Synchronized Manual Sources

Files under [`evander-user-manual-drafts/`](evander-user-manual-drafts/) are section sources for
[`evander-user-manual.md`](evander-user-manual.md). Any factual edit to one must be reflected in
the other in the same change. Automated documentation integrity tests compare their substantive
prose to detect drift.

## Historical Material

The following directories and notes are retained for decision history, not as current behavior
or API references:

- [`superpowers/`](superpowers/): historical specifications and implementation plans
- [`team-mcp-migration/`](team-mcp-migration/): completed Team MCP migration design records
- [`repository-layer-refactor-note.md`](repository-layer-refactor-note.md): earlier repository
  refactor notes
- [`operator-field-help-inventory.md`](operator-field-help-inventory.md): historical field-help
  planning snapshot

## Managed Upstream Documentation

Documentation below `frontend/truman-landing/.agents/` is managed skill or upstream framework
content. Refresh it from its owning upstream source when dependencies change; do not treat it as
an Evander API reference.

## Update Contract

When behavior changes, update the relevant authoritative document, synchronized manual section,
and [`../CHANGELOG.md`](../CHANGELOG.md) in the same change. Run
`./.venv/bin/pytest -q tests/test_documentation_integrity.py` to catch broken local links,
anchors, and inventories that drift from implementation sources.
