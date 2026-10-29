# Admin Web design direction

Discussion draft · 22 September 2026

[Open the interactive concept](admin-web-concept.html). It opens directly in a browser and needs no build, account, network requests, or application backend.

The direction is a calm, compact operational workspace: aligned records, readable type, a restrained palette, and consistent navigation and inspection. Space should help operators compare, read, or act. Each additional border, toolbar, and panel needs a clear purpose.

**Release news remains a prominent dashboard feature.** The operator confirmed that this feature is valuable during the study. The concept gives it a readable article alongside operational attention, with previous/next controls, an expanded reading view, and an archive entry. Its storytelling, images, authorship, practical tips, version information, and changelog access remain part of the intended design.

This document records the original proposal and standalone sketch. That study changed no
application source or runtime state. The subsequent authorized implementation is tracked
in [Admin Web rollout](admin-web-rollout.md); its source and verification belong to the
isolated `feature/admin-design-system` worktree. Nothing has been deployed.

## What I studied

The current checkout was `1242c738` when inspected. The study covered the shared shell and navigation, Dashboard, Sessions, Configuration, the workflow editor, and Team navigation and page structures. The browser sample used current UI source with synthetic data adapted from the repository's smoke fixtures at 1440 × 900. It was not an authenticated inspection of production records.

The Team sample was its Teams/people view. The workflow fixture had two nodes; it demonstrates the surrounding layout, not the readability of a large production graph. The fixture needed a missing default node `config` object and current static Team modules; these adjustments stayed in temporary study tooling. Initial Vite cache conflicts were resolved with an isolated cache.

| Surface | Observed problem | Useful existing work to retain |
| --- | --- | --- |
| Global shell | A roughly 104 px header; several large controls; descriptive copy above the navigation; considerable space spent before reaching the working view. | Directory scope, lock state, grouped navigation, page finder, branding, notifications, theme switching, and deep links. |
| Sessions | A 232 px global rail plus a 280 px session sidebar. Together they use 512 px, about 36% of a 1440 px viewport, before content padding. The initial overview occupies roughly 300 px for six values. | Session access controls, lifecycle actions, repository grants, durable wake history, and disclosures for detailed data. |
| Configuration | Two navigation columns and repeated context/headings push the actual records down the screen. Some components still inherit the older padded panel styling. | Searchable sections, compact `RecordCollection` rows, section layouts, inheritance, explicit save boundaries, and revision conflicts. This is already moving in the right direction. |
| Teams | Fifteen peer destinations wrap across the top. Team selection and team details can repeat the same identity and counts in separate containers. Its own stylesheet differs from the host shell. | Workspace overview, task board, org chart, conversation context, review/release evidence, scoped navigation, and lock enforcement. |
| Workflow | The expanded global rail plus a default 320 px workbench sidebar leave less room for the canvas. Nested sidebar panels hold persistent save actions and small amounts of content. | Graph editing, resizable sidebar, existing focus controls, explicit routing, provider schemas, validation, run inspection, and saved graph geometry. |
| Dashboard | Large section summaries and uneven disclosure states create an uneven overview. Product news has strong visual prominence, but the surrounding operational information is harder to scan. | Release news itself; live status, partial data handling, capacity, approvals, agent inventory, and shortcuts into the underlying work. |

The inconsistency has a concrete source. The main stylesheet mixes the shell's green/blue surfaces with beige feature panels and several type/radius treatments. Team uses a separate Inter/system stack, semantic color variables, and its own panel and button rules. Workflow adds another set of workbench surfaces. A palette change alone would leave the repeated structural space intact.

Relevant source locations:

- [Shell and embedded add-on context](../../frontend/admin-web/src/AdminRoot.tsx), [navigation](../../frontend/admin-web/src/features/admin/AdminNavigation.tsx), and [route inventory](../../frontend/admin-web/src/adminRouting.ts).
- [Shared styles](../../frontend/admin-web/src/styles.css): `.admin-shell-header`, `.admin-shell-layout`, `.feature-page`, `.panel`, `.panel-body`, `.configuration-section-workspace`, and `.admin-workflow-panel`.
- [Dashboard](../../frontend/admin-web/src/features/dashboard/OperationsDashboard.tsx), [Sessions](../../frontend/admin-web/src/features/sessions/SessionsScreen.tsx), [Configuration](../../frontend/admin-web/src/features/configuration/ConfigurationScreen.tsx), and [record collections](../../frontend/admin-web/src/features/admin/RecordCollection.tsx).
- [Configuration field and section inventory](../../src/evander/admin/web/workers_config.py).
- [Workflow editor](../../frontend/admin-web/src/App.tsx).
- [Team markup](../../src/evander_team_addon/ui_static/index.html), [Team styles](../../src/evander_team_addon/ui_static/team-ui.css), and [Team interactions](../../src/evander_team_addon/ui_static/team-ui.js).

Current browser captures are retained outside the repository in the operator-owned review directory.

## General principles

### 1. Give every screen a recognizable structure

Use one global context bar, one page title, a task-specific toolbar, and a working surface. Local navigation belongs directly beneath the title or in a narrow settings navigator when a real hierarchy warrants it. Avoid repeating the page title, directory, description, and selected record inside multiple containers.

Keep global navigation labels and the page finder. The initial rollout should preserve the existing six global navigation groups and all permission/backend-dependent destinations. Page grouping changes deserve a separate decision from typography and spacing.

### 2. Use rows for repeated records

Sessions, agents, repositories, workers, approvals, targets, and audit entries benefit from aligned columns and shared row actions. Favor a thin divider between records. Use cards where their spatial arrangement conveys meaning, particularly task boards, or where a summary has an independent purpose.

Density comes primarily from removing duplicate structure, reducing unnecessary nesting, and choosing the right layout. Body text remains readable. Long titles may wrap, essential identifiers remain inspectable, and potentially destructive actions keep descriptive labels and deliberate confirmation.

### 3. Reveal detail without losing position

Selecting a record opens a consistent inspector. It shows identity, state, key facts, recent activity, and the most useful next actions. A full record provides room for long policy forms, transcripts, evidence, and durable history.

On closing the inspector, restore focus, selection, filter, sorting, and scroll position. Encode important record selection in the URL. Persist user column preferences separately from shared configuration. A long editing task should not be forced into a narrow drawer.

### 4. Make the important differences visible

Give status text and color a single meaning. Active, waiting, blocked, completed, and unavailable must be distinguishable without color alone. Distinguish a valid empty result from loading, an error, a stale snapshot, or incomplete data.

Reserve strong color for actions, selection, and exceptional state. Treat ordinary metadata quietly. A list of twenty healthy rows should not become twenty competing green badges. The prototype explores badges; production could simplify repeated healthy states further.

### 5. Use whitespace deliberately

Prose, release news, chat, and graph geometry need room. Reading surfaces can have a controlled line length while tables use available width. A small fixed set of values belongs in a compact definition list. Avoid minimum heights that stretch simple metadata or empty panels.

Do not manufacture content to fill the screen. An empty inspector can close. An empty collection can show a short explanation and relevant action without reserving a large card.

### 6. Preserve operator context and trust

Directory scope and privilege state stay visible globally. Inherited values show their source; overrides are explicit. Draft changes remain visible until saved or discarded. Revision conflicts offer comparison/reload without silently overwriting another operator.

Locked mutation entry points are disabled with an accessible explanation; search, reading, inspection, and navigation remain available. Preserve server authorization, scoped Git/approval behavior, and revision-gated APIs. A compact interface must still tell the operator what an action affects.

### 7. Share the system across the host and add-ons

Use the same semantic tokens and control states in React screens, Team's static UI, and embedded workbenches. The iframe boundary is an implementation constraint, not a reason to vary the visual language.

The host already passes theme and privilege context to add-ons. Keep origin/source checks and sensible standalone theme defaults; spacing is consistently compact. Generated Team bundles must participate in the eventual rollout.

## Proposed visual system

These are starting dimensions to evaluate, not a blanket CSS scaling operation.

| Element | Starting rule |
| --- | --- |
| Palette | Cool neutral canvas, solid content surfaces, subtle dividers, a muted teal accent consistent with the existing product. Equivalent semantic colors in dark mode. |
| Typography | One system sans stack for controls and records. Approximately 14 px body, 13–14 px dense table text, 12 px supporting metadata, 22 px page titles. Monospace for identifiers and tabular figures for numeric comparisons. |
| Reading | Release articles and long prose use a more generous line height and bounded line length. Headings can be more expressive without changing the control vocabulary. |
| Spacing | A 4 px base; 8 px within controls, 12–16 px within related groups, 24 px between distinct working areas. |
| Context bar | 48 px at ordinary desktop sizes; allow expansion/reflow where content requires it. |
| Global navigation | Approximately 208 px expanded. Keep collapse and page search; show tooltips/accessible names when collapsed. |
| Controls | About 32 px in the compact layout. Larger touch targets for primary navigation and actions. |
| Data rows | 40 px minimum for one-line rows; natural height for wrapped content. |
| Corners | Square controls, surfaces, dialogs, and graph nodes. Rounding is reserved for pills and badges, avatars, status dots, circular controls, graph connection handles, dials, and switches. |
| Elevation | Borders and surface tones for persistent regions. Shadow mainly for drawers, menus, and dialogs. |
| Inspector | About 340–400 px when space permits; full record view available. At narrow widths use an accessible sheet or dedicated page. |
| Motion | Short state transitions where useful. Respect reduced-motion preferences. Live updates should not move the selected row or steal focus. |

Keep the configured Evander/company branding and custom header assets in implementation. The sketch's simple wordmark is a placeholder, not a proposed rebrand.

For implementation, use shared tokens such as `surface.canvas`, `surface.content`, `text.primary`, `text.secondary`, `border.default`, `action.primary`, `state.waiting`, `space.3`, and `control.height`. Reuse a small set of primitives: page header, toolbar, field row, collection/table, status, inspector, empty/error state, dialog, and save bar.

## Five page sketches

| Page | Proposed layout | Discussion point |
| --- | --- | --- |
| Dashboard | A compact metric strip; attention list and a readable release article side by side; compact health/capacity; recent work. The article opens into a full reading view and supports previous/next browsing. | Balance article and operational column widths. A remembered personal arrangement could be useful later. News remains visible by default. |
| Sessions | Search and state filter above a comparable inventory. Selection opens an inspector. Full session has Overview, Access, Wake schedules, and Activity sections. | Whether selection itself opens inspection or the operator uses an explicit Inspect action. The sketch supports both row-name and arrow entry points. |
| Teams | A quieter area navigation, team scope, and task list with an optional board. Both open the same task inspector showing outcome, blockers, ownership, and evidence. | Validate the proposed grouping against everyday use before moving destinations. |
| Workflow | A compact pack/action toolbar, central graph, optional navigator, inspector on selection, and collapsible run tray. | Evaluate with a real large graph and long node forms before choosing final panel widths. Graph coordinates and behavior remain preserved. |
| Configuration | Narrow searchable section navigation; aligned records for collections; label/value rows for settings; explicit inheritance; persistent save feedback. | Preserve current save scopes and every backend field. The sketch demonstrates targets and runtime fields only. |

The Team grouping is exploratory and retains all fifteen existing views:

| Proposed area | Existing destinations |
| --- | --- |
| Workspace | Workspace |
| Work | Projects, Task board, Cases, Delivery, Planning |
| People | Teams, Org chart |
| Communication | Chat, Mail |
| Governance | Governance |
| Activity | Operations, Activity, Activity timeline, Memory |

Memory's placement deserves discussion: a dedicated Knowledge area may be clearer if it is frequently used. Preserve deep links, direct access through Find page, counts that flag urgent work, and the operator's selected team/project. Grouping must not hide pending reviews or releases.

## How this extends to the other pages

| Pattern | Other screens it should serve |
| --- | --- |
| Overview | Team Workspace, reports overview, capacity summaries |
| Collection + inspector | Agents, Issues, Approvals, Artifacts, Git repositories, VMs, Workers, Subscriptions, Tool calls, Users & groups |
| Record + section navigation | Session policy, approval decision, worker details, repository index, user/group permissions |
| Settings | Connectors, Host directories, MCP tools, Tool visibility, Add-on configuration, Skills metadata |
| Workbench | Org chart, conversation/mail, workflow editor/run inspection, detailed reports |

This is a layout vocabulary. Individual tasks still determine appropriate content: a chat transcript, a review decision, and a graph editor need different working surfaces.

## Responsive behavior and states

- At broad desktop widths, allow an inspector beside the record list. At laptop widths, keep enough readable columns and allow the detail pane to expand. Table overflow stays inside the table.
- At narrow widths, provide navigation through an accessible menu/page finder. Prioritize record identity, state, and the key action; expose other fields in the record. A board or graph may pan within its own surface. Long articles and forms reflow.
- Make all visible actions usable from the keyboard. Dialogs and sheets need focus handling, Escape, focus restoration, labels, and a clear relationship to the selected record. Do not make dragging the only way to move work or manipulate a graph.
- Validate text contrast, focus indication, touch targets, 200% text enlargement, and narrow reflow. WCAG 2.2's minimum target guidance starts at 24 × 24 CSS px with defined exceptions; the design aims larger for frequent actions. Its reflow guidance permits genuinely two-dimensional surfaces, including data tables, to have their own scrolling region. See [W3C target guidance](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html) and [W3C reflow guidance](https://www.w3.org/WAI/WCAG22/Understanding/reflow.html).
- Put loading, error, stale, and partial-data messages at the affected surface. A failed capacity request should not erase the news or task list. Announce significant state changes without a continuous stream of distracting announcements.

The standalone concept is a visual and interaction study, not an accessibility conformance claim or a complete mobile implementation.

## Rollout to discuss

1. **Agree on the direction.** Review the five sketches at the screen sizes actually used. Settle the news/attention balance, default density, inspector behavior, and Team grouping separately.
2. **Build shared foundations.** Add semantic tokens, controls, spacing/type rules, and explicit state treatments. Cover the React shell and a small Team surface so the iframe contract is exercised early.
3. **Pilot Sessions.** Prove the collection/inspection pattern on a real inventory and full record. Validate long identifiers, large datasets, locks, permissions, deep links, and focus/scroll restoration.
4. **Move repeated collections and Configuration.** Reuse `RecordCollection` work. Compare the before/after field inventory against `workers_config.py`; keep save scopes, inherited values, raw/effective views, and conflict behavior intact.
5. **Move Teams in coherent groups.** Start with task/people inspection, then delivery/reviews, conversation surfaces, and diagrams. Preserve all destinations and outstanding-work visibility.
6. **Refine the workflow workbench and Dashboard.** Test real graph sizes and node schemas. Give release news and operational information their agreed space; handle asynchronous data independently.

Prefer small, reviewable changes. Keep each migrated page internally consistent. Use screenshots at fixed viewports and focused interaction tests for the changed behaviors. Each eventual feature or fix branch follows the repository's version and changelog rule; this discussion artifact is not a release branch.

### Acceptance evidence for implementation

- Compare the same records, viewport, theme, and density before and after. Measure the first useful record position, fully visible rows, graph working area, and navigation width. Report raw measurements instead of claiming a universal percentage improvement.
- Check real workflows: find a session and its next wake; understand a blocked task; review evidence; edit an inherited setting; follow a workflow run; read the latest release story and return to the dashboard.
- Confirm there are no missing configuration fields, destinations, permission checks, lock restrictions, revision protections, or live data states.
- Verify narrow/wide layouts, both themes, keyboard flow, long labels, loading/error states, and useful focus restoration. Use explicit timeouts for browser checks.
- Rebuild the supported application/add-on asset surfaces only when rolling out implementation, and verify the served asset chain. Keep deployment an explicit later step.

## Using the sketch

The five working navigation destinations are Dashboard, Teams, Sessions, Workflow, and Configuration. Other navigation entries open a short coverage note so the full information architecture stays visible.

Try light/dark; search/filter sessions and tasks; open and close inspectors; switch the task list to a board; inspect workflow nodes and show/hide runs; open release stories and browse between them. Configuration has Communication targets and Agent runtime sketches. Unlocking affects only sample editing in the page; it never authenticates to a service or writes to live state.

Full policy forms, backend saves, real graph editing, all Team subviews, notification content, and configuration schema coverage are outside the standalone sketch. Those entry points explain their intended destination. The study does not claim these functions are implemented.

### Study verification

The five sketches were opened in Chromium at 1440 × 900 and 390 × 844. The dashboard was also captured at 1280 × 800. The browser pass reported no JavaScript page errors or page-wide horizontal overflow at the narrow size; tables and the graph keep their own scrolling regions.

Checked news expansion and previous/next browsing, session search and filter preservation on inspector close, task list/board record parity, node inspection, locked setting controls, sample override/save/readback across section changes, theme switching, and row height. The density selector was removed on 23 September at the operator's request; the prototype and implementation now always use compact spacing. This verifies the standalone prototype's demonstrated interactions, not application behavior or complete accessibility.

Proposed captures are retained outside the repository in the operator-owned review directory.
