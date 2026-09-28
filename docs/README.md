# Documentation Map

This directory contains current operator and developer references, focused subsystem guides,
generated manual publications and their canonical sources, historical design records, and managed upstream
documentation. Start with the task below, then use the subsystem references for details.

| Task | Start with |
|---|---|
| Install or operate a host | [Operations](operations.md), [Ansible](../ansible/README.md), [live smoke tests](live-smoke-tests.md) |
| Use Admin Web | [Admin UI manual](admin-ui-user-manual.md) |
| Configure a deployment | [Configuration reference](configuration-reference.md), [configuration migrations](config-migrations.md) |
| Choose where agents execute | [Execution backends](execution-backends.md), [container workers](container-worker-backend.md) |
| Build and run a team | [Team Add-on](addons.md#team-module-guidance), [progress boards](case-progress-boards.md), [case context](case-context.md) |
| Develop or verify a change | [Architecture](architecture.md), [implementation boundaries](maintainability-boundaries.md), [testing](testing.md) |

## Authoritative Entry Points

- [`../README.md`](../README.md): project overview and first-run path
- [`operations.md`](operations.md): host installation, service operation, and recovery
- [`architecture.md`](architecture.md): current control-plane architecture and trust boundaries
- [`configuration-reference.md`](configuration-reference.md): host, directory, provider, and
  service configuration
- [`admin-ui-user-manual.md`](admin-ui-user-manual.md): supported Admin Web workflows
- [`evander-user-manual.md`](evander-user-manual.md): complete illustrated technical manual for
  core Evander and the Team Add-on; also published as
  [`HTML`](evander-user-manual.html), [`PDF`](evander-user-manual.pdf), and
  [`DOCX`](evander-user-manual.docx)
- [`addons.md`](addons.md): add-on lifecycle, bundled add-ons, and Team action reference
- [`connectors.md`](connectors.md): named file/REST capabilities, authentication, grants, and
  custom connector drivers
- [`host-directories.md`](host-directories.md): scoped host filesystem resources, session grants,
  artifact imports, quarantine recovery, and file-added triggers
- [`execution-backends.md`](execution-backends.md): backend selection and shared contracts
- [`container-worker-backend.md`](container-worker-backend.md): worker VM/container deployment
- [`git-remotes.md`](git-remotes.md): managed repository and restricted Git flow
- [`autonomous-git-recovery.md`](autonomous-git-recovery.md): typed Team Git incidents, bounded agent recovery, preservation, and rollout
- [`live-smoke-tests.md`](live-smoke-tests.md): executable live verification procedures

## Focused References

### Models, data, and integrations

- [`classification.md`](classification.md): core classification providers, reusable profiles,
  workflow and agent evaluation, editor testing, and usage records
- [`openrouter.md`](openrouter.md): approved Codex model routes, host credentials, relay restrictions,
  provider-reported charges, and activation checks
- [`managed-website-sessions.md`](managed-website-sessions.md): granted browser login and renewal,
  host credential storage, and session-bound browser state
- [`repository-indexing.md`](repository-indexing.md): scoped code search, navigation, worker
  scheduling, baseline reuse, and exports
- [`repository-indexing-validation.md`](repository-indexing-validation.md): reproducible indexing
  benchmarks and verification
- [`miro-addon.md`](miro-addon.md): editable boards, collaboration, and workflow monitoring
- [`figma.md`](figma.md): native mockups, parallel proposals, review, and immutable snapshots
- [`alexa-agent-bridge.md`](alexa-agent-bridge.md): asynchronous personal Alexa bridge
- [`teams-meeting-media-bridge.md`](teams-meeting-media-bridge.md): Teams meeting media bridge

### Workflows, Team, and recovery

- [`case-progress-boards.md`](case-progress-boards.md): case filtering, progress permalinks, and ADO intake
- [`case-context.md`](case-context.md): decision journals, context modes, and bounded wake deltas
- [`workflow-document-patches.md`](workflow-document-patches.md): workflow graph edits through revisioned patches
- [`release-recovery-and-verifier-policy.md`](release-recovery-and-verifier-policy.md): rejected
  verification recovery, generation limits, and structured verifier outcomes
- [`team-data-boundaries.md`](team-data-boundaries.md): project and organization data ownership
- [`team-context-reduction-runbook.md`](team-context-reduction-runbook.md): context reduction rollout and checks
- [`team-addon-wake-decision-tree.md`](team-addon-wake-decision-tree.md): wake scheduling and dispatch

### Engineering and operations

- [`testing.md`](testing.md): focused and complete Python/frontend test commands, deadlines, and ledgers
- [`config-migrations.md`](config-migrations.md): configuration migration history and procedures
- [`conformance-reports.md`](conformance-reports.md): conformance report schema and API
- [`trigger-conformance.md`](trigger-conformance.md): trigger and workflow conformance procedures
- [`libvirt-vm-template-operations.md`](libvirt-vm-template-operations.md): libvirt template and
  storage operations
- [`workflow-run-inspector-demo.md`](workflow-run-inspector-demo.md): workflow run-inspector demo
- [`orchestration-latency.md`](orchestration-latency.md): latency diagnosis across workflow stages
- [`performance-hotpaths.md`](performance-hotpaths.md): implemented performance work and its boundaries
- [`maintainability-boundaries.md`](maintainability-boundaries.md) and
  [`cli-module-boundaries.md`](cli-module-boundaries.md): source ownership and extension seams

## Technical Manual Sources And Publication

The canonical chapter, diagram, style, index, and publication metadata sources live under
[`evander-user-manual/`](evander-user-manual/). Do not edit the generated Markdown, HTML, PDF, or
DOCX independently. Rebuild all four formats and their provenance record with:

```bash
./.venv/bin/python scripts/build_evander_user_manual.py
```

Verify that tracked publications still match their sources with:

```bash
./.venv/bin/python scripts/build_evander_user_manual.py --check
```

The build requires `pandoc`, `google-chrome`, and the local Mermaid CLI. It renders diagrams,
assembles chapter sources, and writes Markdown, embedded HTML, PDF, DOCX, and a hash-based build
record. Update `evander-user-manual/book.json` when refreshing the documented version, publication
date, or verified source revision. `--check` verifies source/output fingerprints; run the tests
below as well to check links and assets.

Mermaid rendering dependencies are pinned in the source directory's `package-lock.json`; install
them with `PUPPETEER_SKIP_DOWNLOAD=1 npm ci --ignore-scripts` from that directory. Sanitized dark
screenshots are regenerated by `scripts/capture_evander_user_manual_screenshots.mjs`.

## Historical Material

The following directories and notes are retained for decision history, not as current behavior
or API references:

- [`superpowers/`](superpowers/): historical specifications and implementation plans
- [`team-mcp-migration/`](team-mcp-migration/): completed Team MCP migration design records
- [`repository-layer-refactor-note.md`](repository-layer-refactor-note.md): earlier repository
  refactor notes
- [`operator-field-help-inventory.md`](operator-field-help-inventory.md): historical field-help
  planning snapshot
- [`design/`](design/): Admin Web design and rollout records
- [`test-suite-review-2026-09-24.md`](test-suite-review-2026-09-24.md): dated test-pruning review,
  not a current suite result

## Managed Upstream Documentation

Documentation below `frontend/evander-landing/.agents/` is managed skill or upstream framework
content. Refresh it from its owning upstream source when dependencies change; do not treat it as
an Evander API reference.

## Update Contract

When behavior changes, update the relevant authoritative document, canonical manual chapter, and
[`../CHANGELOG.md`](../CHANGELOG.md) in the same change, then rebuild the publications. Run
`./.venv/bin/python scripts/build_evander_user_manual.py --check` to verify publication freshness,
and `./.venv/bin/pytest -q tests/test_documentation_integrity.py tests/test_evander_user_manual.py`
to check local links, anchors, assembled links, diagrams, and screenshot assets.
