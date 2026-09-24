# Admin Web rollout

Implemented locally · 23 September 2026 · version **0.61.0**

The authorized full rollout covers all 21 built-in destinations, six standalone add-on UIs,
and shared dialogs and controls. Release news remains prominent, with images, authorship,
a readable article view, archive navigation, and changelog access.

Branch: `feature/admin-design-system`.
Starting main: `6b88a08b8cd8423d2737e9670a467df44669b1ed`.
Integration base: `c564a87bf9533fe02027fe1b72e1a3f8c96b338e` (`example/main`).
The implementation has passed the full local test suites and is ready for publication.
No deployment or live configuration/workflow change is part of this work.
The original study and browser evidence are preserved outside the repository.

## Design and implementation

### Navigation follow-up · 24 September 2026

The New UI now opens record management pages directly in the sidebar/detail layout.
Agents, Sessions, Issues, Approvals, Workers, VMs, Skills, Add-ons, Connectors,
Host directories, Git repositories, and Users/groups start with a selection card.
MCP Tools follows the same pattern. One sidebar click opens the complete details;
links to specific items still open them directly, including older inspector URLs.
Search and filters remain available beside the details. Upload, registration, and
directory configuration use compact disclosures. Classic retains its frozen layout.

The inventory/inspector descriptions and verification below document the original
0.61.0 rollout; this follow-up replaces that intermediate step on these record pages.

### Original rollout

The [original direction](admin-web-direction.md) and [interactive concept](admin-web-concept.html)
record the study. The implementation uses shared semantic colors, typography, spacing,
controls, light/dark themes, and consistent compact spacing.

- Comparable records use aligned lists, dividers, and a closable inspector. Long
  forms and evidence remain available through **View agent**, **View session**, and
  equivalent page-specific actions. The list and detail view
  stay mounted to retain filters and local state.
- A temporary **Classic / New UI** header selector defaults to a frozen snapshot of main.
  It remembers the choice, preserves the current URL, confirms before switching, and
  loads only the selected application's assets and matching built-in add-ons. Both
  versions use the same authenticated backend. Packaging and removal are documented in
  [classic/README.md](../../frontend/admin-web/classic/README.md).
- The global rail is 208 px. Controls and one-line rows start at 32/40 px.
  Coarse pointer controls retain 44 px targets. There is no density selector.
- Inspectors are companion panes on desktop and native dialogs on narrow screens.
  Keyboard close, focus restoration, and narrow dialog Tab navigation are handled.
- Workflow commands sit above the canvas; the default rail is 264 px and remains resizable.
  Graph geometry, editing, run inspection, validation, and revision-gated saves remain.
- Team retains all 15 destinations. A compact tab strip becomes a selector on narrow
  screens. Work records are flatter; boards, graphs, conversations, and reading layouts
  retain the space their tasks require.
- Shared configuration continues to render the backend field inventory, inheritance,
  validation, dirty state, and conflict behavior. Mutations retain existing API
  authorization and revision boundaries.
- Canonical assets live in [src/design](../../frontend/admin-web/src/design/README.md).
  The build distributes matching CSS and a theme/privilege context bridge to
  six standalone add-ons. Generated copies are checked for drift.

## Surface coverage

Every row has implementation and rendered or source evidence. Verification scope is stated
explicitly. Browser checks used populated synthetic fixtures, not production data.

| Surface | Implemented design | Verification |
| --- | --- | --- |
| Shell, directory, identity, lock, navigation | Compact shell and controls; responsive navigation; shared appearance | Desktop/phone in both themes; page finder and keyboard checks; compact row heights |
| Login, deployment/loading/error surfaces | Shared type, controls, spacing and state colors; single-column login | Login at both widths; existing state paths retained; asset readiness and missing-data checks |
| Dashboard | Metric strip, attention/status rows, prominent release story and reader | Both themes/widths; deep links, missing news, article images, archive, Escape and focus return |
| Sessions | Searchable inventory, inspector, full access/wake record; selected-record loading guard | Session/Agent smoke batch: 11 passed; narrow inspector keyboard/close checks; full forms rendered |
| Agents | Comparable inventory and inspector; full record and creation/policy/log dialogs | Source-contract/navigation checks; creation flow; both themes/widths |
| Issues | Inventory, inspector, compact full record | Focused navigation check; full record at desktop/phone |
| Approvals | Inventory, inspector, retained evidence and decision record | Empty queue/navigation check; full record at desktop/phone |
| Artifacts | Tree/table, inspector, upload entry | Upload check; populated captures at both widths/themes |
| Reports | Horizontal local tabs, flat metrics, report workbench/detail pane | Populated report captures at both widths/themes; existing report behavior retained |
| Workflow | Command bar, narrower default rail, source-file disclosure, shared fields/workbench | Read-only, undo/unsaved, run detail and tutorials; mobile scrolling, two-finger pan, node interaction and editing |
| Skills | Inventory, inspector, compact full record, locked mutation controls | Full records; Replace/Delete disabled while locked; no mutation requests |
| Add-ons | Inventory, inspector, compact configuration, locked enablement/runtime-pack controls | Full records; locked enablement disabled; no mutation requests |
| Connectors | Inventory, inspector, full configuration | Both themes/widths; full records; inherited configuration retained |
| Host directories | Inventory, inspector, grant/recovery record | Both themes/widths; full records |
| Git repositories/index | Inventory, inspector, registration/maintenance record; narrow forms stack | Registration/index history checks; full records; final phone overflow fix verified |
| Subscriptions | Profile rows with disclosures; collapsed creation form | Profile identity/capacity check; both themes/widths |
| VMs | Inventory, inspector, retained actions and full record | Populated VM route in both themes/widths; full records |
| Workers | Inventory, inspector, compact details | Focused navigation check; full records and both themes/widths |
| Tool visibility | Horizontal session picker and shared policy workbench | Effective policy inspector check; both themes/widths |
| MCP tools | Inventory, inspector, full policy/approval record | Full records and both themes/widths |
| Tool-call audit | Horizontal filters, comparable records, inspector | Three focused checks; run-to-audit navigation; both themes/widths |
| Configuration | Compact navigator, shared field/collection rules; unchanged backend metadata | Five collection checks covering drafts, conflicts, inheritance and narrow forms; grouped-field backend regression |
| Users/groups | Inventory, inspector, flat permission forms | Full records and both themes/widths |
| Assistant, notifications, personal connections | Shared dialogs, message/reader type, compact controls | Four assistant checks; draft preservation, notifications and personal connection dialog at both widths |
| Team Workspace, Operations, Delivery, Memory | Flat operational sections, evidence disclosures, compact records | All four in both themes/widths; focused Team behavior; populated Memory readback |
| Team Projects, Task board, Cases, Teams | Compact records/board, filter disclosure, optional closable Teams inspector | Both themes/widths; project/board checks; inspector Escape/focus; populated Cases readback |
| Team Chat, Mail, Activity, Governance | Conversation/readers and compact operational rows | Both themes/widths; mobile Chat, historical Mail and Governance creation; populated Mail readback |
| Team Planning, Activity timeline, Org chart, agent creation | Shared workbench/dialog rules; retained graph hierarchy/geometry | Both themes/widths; planning, snapshot-only timeline, Org context and create/register checks; graph controls inspected |
| Bokio | Shared appearance and compact integration settings/workspace | Desktop/phone in both themes; live context/lock behavior; static UI regression |
| Skatteverket | Shared appearance, connection disclosure and compact forms | Desktop/phone in both themes; context, draft/lock checks; connection-editor regression |
| MSSQL seed | Shared appearance, settings and operational workspace | Desktop/phone in both themes; context/locked controls; lock regression |
| Miro | Shared appearance, compact configuration/workspace, inherited connection guard | Desktop/phone in both themes; live context, lock and draft checks |
| Figma | Shared appearance and compact connection/configuration forms | Desktop/phone in both themes; live context, lock and draft checks |
| ADO activity, Elastic Stack, Freshservice | Converted shared MCP, workflow-provider and connector settings | Source inventory confirms no independent static UI; shared host surfaces covered above |

## Verification evidence

### Browser review

Chromium captures covered **84 main-page combinations** (21 destinations × two themes ×
1440/390 px widths) and **60 Team combinations** (15 views × the same variants). Final
capture results reported no JavaScript page errors or page-wide horizontal overflow.
Tables, boards, graphs, and conversations can retain their own scrolling regions.

Additional checks covered:

- 14 shared-surface scenarios: session inspection/focus/Tab/Escape/full record, release
  reader images/archive, compact layout, personal connections, notifications,
  assistant draft preservation, and login.
- 22 full-record renders across desktop/phone plus two locked Skills/Add-ons checks.
  Git's phone form initially overflowed; its final readback and the Users phone refinement
  passed. Locked checks issued no mutation requests.
- 12 final Team checks covering populated Memory/Cases/Mail, Org controls, inspector focus
  restoration, and agent creation at both widths. Captures taken during loading were
  replaced after waiting for content.
- Six focused add-on checks covering the other five standalone UIs and the actual host
  iframe context path, including appearance propagation and lock/draft behavior.

Representative screenshots include Dashboard, release reader, Sessions, Workflow,
Configuration, Teams, Mail, and phone agent creation. They are retained outside the
repository under `/tmp/evander-admin-design-review/evidence/playwright`.

Capture scripts and raw logs are local review artifacts under
`/tmp/evander-admin-design-review`. They use repository smoke fixtures with additional
synthetic records and stories. They are not production monitoring evidence.

### Focused automated checks

Focused passing checks cover Sessions/Agents, Configuration collections, Workers/Issues,
Artifacts upload, Dashboard/news, navigation, Tool visibility, Tool calls, assistant,
workflow permissions/undo/mobile/run detail/tutorials, and Team views/dialogs.

| Final bounded batch/log in the local review directory | Result |
| --- | --- |
| `team-final-checks.log` | 12 passed |
| `team-bundles-final.log` | 7 passed after rebuilding the Team bundles |
| `mobile-and-addons-final.log` | 7 passed (six add-on checks plus mobile workflow) |
| `navigation-final.log` | 4 passed |
| `gesture-dashboard-checks.log` | 2 passed |
| `python-final.log` | 9 passed in 1.29 s |

The Python batch covers asset readiness, grouped Configuration fields, authenticated
add-on routes, and Skatteverket/MSSQL seed/Bokio static UI regressions:

```sh
EVANDER_CHECKOUT=/path/to/primary-checkout
PYTHONPATH=/tmp/evander-admin-design/src timeout 60s \
  "${EVANDER_CHECKOUT}/.venv/bin/pytest" -q \
  tests/test_admin_web_frontend_assets.py \
  tests/test_admin_web.py::test_admin_web_configuration_state_exposes_grouped_tabs \
  tests/test_admin_web_addons.py::test_admin_web_addon_ui_routes_follow_admin_auth \
  tests/test_skatteverket_addon.py::test_skatteverket_static_ui_exposes_connection_editor \
  tests/test_mssql_seed_addon.py::test_mssql_seed_ui_guards_settings_mutations_when_locked \
  tests/test_bokio_addon.py::test_bokio_ui_tracks_live_privilege_and_executing_recovery_state
```

The initial sandbox execution stopped after the four asset tests at its 60 s bound.
Repeating this exact batch outside the sandbox passed all nine tests.

### Build and distribution

From `frontend/admin-web`, all of these passed:

```sh
timeout 120s npm run build
timeout 120s npm run build:team-org-flow
timeout 120s npm run build:team-agent-create
npm run check:design-assets
```

The final host build passed TypeScript compilation. All **34 manifest entries** resolve
to existing files, `admin_frontend_assets_ready` succeeds, and all six add-ons reference
their generated CSS/JS pairs. Static route allowlists and package data include those assets.
The Team agent creation bundle was regenerated from current source; the Org bundle build
succeeded without changing its output.

`dist/assets` remains generated/ignored under the existing repository convention. A
deployable checkout must build and carry these assets; the tracked `dist/index.html` alone
is insufficient. Version files are synchronized at 0.61.0 with one matching changelog entry.
The user manual and canonical design maintenance notes are updated.

### Initial verification limits

The broad frontend command `timeout 150s npm test` timed out with exit 124. Its last
reported results were 279 passing, eight failing, and two cancelled tests. Failures included
obsolete labels, navigation/disclosure expectations, and a source marker; these were
corrected and focused reruns passed. The cancelled tutorial checks also passed in focused
runs. That initial review used bounded focused reruns. The subsequent full-suite
validation below supersedes this earlier limit. No stalled test process was kept.

The review used local synthetic data and Chromium. It does not establish production data
coverage, every possible provider schema, all browser engines, or complete accessibility
conformance. No live backend mutation or deployment was part of this work.


## Integration with refreshed main

The branch was advanced to the refreshed `example/main`, then the saved redesign was applied
and conflicts resolved before its first feature commit. This preserves the current workflow
editor modules, backend performance work, customer-neutral branding/defaults, and removal of
legacy HTTP-target fields. Compact sidebar dimensions now live in `editorConstants.ts`,
and source-file disclosure uses `documentPanelRenderers.tsx`.

The design study now uses synthetic project/operator identities. Original study artifacts and
browser evidence were moved to operator-owned storage outside the repository. Login styling
uses the current Evander class name. `pyproject.toml`, the package version, and `uv.lock`
all use 0.61.0, with one feature changelog entry above main's 0.60.0 release.

Post-integration verification:

- Host, Team Org, and Team agent-creation builds passed; all six generated add-on asset pairs
  match their canonical sources. All 34 host manifest entries resolve.
- The focused browser batch passed 41 of 42 checks. The Worker return-navigation step was
  updated to open the full record from the inventory; its focused rerun passed, including
  non-overlapping polling, action refresh, and cleanup of in-flight requests on unmount.
- Seven focused Team creation, Org, Governance, Chat, Mail, timeline, and project-policy
  checks passed against the rebuilt bundle.
- 73 Python checks passed, including the nine UI/asset/configuration regressions above and
  the current Admin performance regression suite.
- Final source and staged whitespace checks passed. This integration used bounded focused
  runs; the subsequent full-suite validation below covers the completed branch.

Integration logs use the `integration-` prefix in the external review directory.

## Final full-suite validation

The final local validation on 23 September includes the temporary Classic / New UI
selector, natural record-action labels, and removal of the Compact / Comfortable
selector. New UI and all six add-ons always use compact spacing; existing saved
Comfortable preferences no longer affect layout, and coarse-pointer targets remain larger.

| Command | Result |
| --- | --- |
| `timeout 1200s ./.venv/bin/pytest -q` | 8,453 passed, seven warnings, 630.42 s |
| `timeout 300s npm test` | 295 passed, no failures or skips, 36.85 s |
| `timeout 300s npm run test:smoke` | 167 passed, no failures or skips, 152.22 s |
| Final affected Python UI/asset batch | 51 passed, 1.94 s |
| `timeout 120s npm run build` | TypeScript, New UI, and frozen Classic installation passed |
| `npm run check:design-assets` | All six generated add-on asset pairs match their sources |

The full Python suite preceded the final frontend-only fixes; the affected Python
UI/asset batch was rerun against the final build. Frontend commands ran from
`frontend/admin-web`, with the two full frontend suites run sequentially.

The earlier 300 s smoke run timed out while obsolete workflow fixtures were failing.
Those fixtures now use the current nested node `config` contract. Browser tests now open
the relevant inspectors and disclosures, wait for loaded subscription choices, and
assert current review payloads and permissions. Existing checks also caught and now
cover fractional cost totals, Chat revision refresh, and reduced-motion Org chart
visibility. The final full smoke run passed every test within its bound.

Production-build browser checks verify Classic as default, URL and per-tab selection,
remembered preferences, cancellation with drafts intact, native unsaved-change vetoes,
fallback switching when the app fails to load, and matching add-on assets. Request
assertions confirm that each version loads only its own application bundle.

Logs are retained locally as `/tmp/evander-ui-full-pytest.log` and
`/tmp/evander-ui-compact-{build,full-node,full-smoke,python-ui}.log`.
The synthetic-data and browser-coverage limits above still apply.
