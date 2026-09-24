# Admin Web design

This directory is the canonical visual system for the host React application and six
standalone add-ons. See `docs/design/admin-web-direction.md` and the rollout ledger.

- `tokens.css` defines semantic colors, type, and compact spacing. Use the same token
  for the same meaning in light and dark themes.
- `baseline.css` puts the historical host stylesheet in `admin-structure`. It retains
  component geometry and behavior; the focused files here own current visual rules.
- `controls.css`, `layout.css`, and `collections.css` define controls, the shared shell,
  inventories, and inspectors. The other stylesheets cover workbenches and readers.
- `RecordWorkspace` retains the inventory and full form while switching views.
  `RecordInspector` is a companion pane on desktop and a native modal on narrow screens.
  Record selection stays in the URL; close restores focus to the triggering record.
  Inventory column headers toggle ascending/descending sorting. Give each cell its raw
  `value` (numeric counts/bytes or parsed timestamps) and optional rendered `content`;
  never sort formatted display strings or badges. Missing values stay last.
- `addon-context.js` consumes same-origin parent theme and privilege context, with
  standalone theme defaults. Host and add-ons always use compact spacing.
- `workflow.css` keeps node types recognizable with amber triggers, green gates,
  blue effects, and purple queries in both themes. Type bands, tinted surfaces, and
  text labels remain visible alongside selection, diagnostics, and execution badges.

## Build and distribution

From `frontend/admin-web`:

```sh
npm run build
npm run build:team-org-flow
npm run build:team-agent-create
npm run check:design-assets
```

The host build runs `sync:design`. It generates `admin-design.css` and
`admin-design.js` under each add-on's `ui_static` directory. Each generated stylesheet
imports its existing structural styles into a lower-priority CSS layer. Update the
canonical sources here, then regenerate; do not edit the copies independently.

During the temporary rollout the host build also installs the frozen Classic asset
package. See `frontend/admin-web/classic/README.md` for version selection, deployment
requirements, and the complete removal steps.

The add-on assets are package data and have explicit route allowlists where required.
Host `dist/assets` remains generated/ignored according to the existing build process;
a deployable checkout must run the host build and include its referenced assets. The
tracked `dist/index.html` alone is insufficient.

## Working rules

Prefer aligned rows and dividers for comparable records. Keep forms, prose, chat, graphs,
and boards in the geometry their tasks require. Loading, empty, failed, partial, inherited,
locked, and unsaved states must remain distinct. Preserve backend field metadata,
revision checks, authorization, and disclosure state when changing presentation.

Keep targets reachable by keyboard and at narrow widths. Native modal dialogs must
block background shortcuts; nested scrolling belongs inside a real table, graph, or
conversation. Check a representative populated view in both themes before accepting
a selector change: an imported stylesheet does not prove a surface is converted.
