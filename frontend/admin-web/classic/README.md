# Temporary Classic / New UI rollout

Classic is frozen from `c564a87bf9533fe02027fe1b72e1a3f8c96b338e` (the main
revision before the redesign). `assets.zip` contains its compiled host application
and the static files for the six built-in add-ons. `manifest.json` records the source
revision and SHA-256 checksums. The only host adaptation sends built-in add-on iframes
to their matching `/addons/<id>/classic/` assets.

This is a frozen fallback, not a second maintained application source tree. Both UIs
use the same authenticated APIs, privilege state, and live data.

## Build and behavior

`npm run build` builds New UI, verifies the frozen archive, and installs Classic to
`dist/classic` and `.classic-addons`. No Git or network access is needed to install
the frozen version. Include the generated `.classic-addons` directory when copying
a prepared deployment instead of building on the target. The frontend readiness check
also checks the frozen asset inventory.

The server chooses the document before returning HTML:

1. `?admin_ui=classic` or `?admin_ui=new` explicitly selects the version for a tab.
2. Otherwise, the `admin_ui_version` preference cookie is used.
3. Otherwise, New UI is the default.

The shared header selector preserves the rest of the URL and confirms before
reloading, giving the operator an opportunity to save changes. Existing browser
unsaved-change guards still run. The preference is remembered after the destination
loads successfully. Each loaded tab has an explicit version in its URL, so tabs can
show different versions without changing each other on refresh.

If the application fails to render its header, the selector appears on its own in the
top right so the operator can still switch versions. A missing bundle does not block
serving the other version; deployment readiness checks still require both bundles.

Only the selected application's JavaScript and styles are referenced in the response.
The shared selector is a small standalone script/style pair. Classic add-ons are served
through authenticated `/addons/<id>/classic/` routes, outside the public frontend
static directory. New UI's iframe URLs explicitly select New UI. Standalone built-in
add-on URLs accept the same query preference; existing standalone URLs default to New
when there is no remembered preference.

Vite development continues to run the current source application. Use the built
Admin Web server to test the version selector and frozen Classic.

To intentionally replace the snapshot, first verify the chosen revision, then run:

```sh
python3 scripts/classic-ui.py freeze --revision <full-commit-sha>
npm run build
```

The freeze step uses the installed frontend dependencies and never installs packages.
Review the source revision, lockfile compatibility, archive, and manifest together.

## Remove after acceptance

1. Delete this directory, `scripts/classic-ui.py`, and `public/ui-rollout`.
2. Remove the Classic install command from `package.json` and its ignore entry.
3. Delete `src/evander/admin/web/ui_rollout.py`; make the document route serve `dist`
   directly, and restore direct mounting of add-on UI routes.
4. Remove the `admin_ui` addition in `AdminRoot.tsx`, the optional Classic prefix/asset
   root support, and `ui-rollout.json` readiness handling in the frontend asset helper
   and `scripts/install_systemd_units.sh`.
5. Delete the rollout-specific tests and fixture, rebuild, and remove generated
   `dist/classic` and `.classic-addons` from prepared deployments.

Existing `?admin_ui=...` bookmarks and cookies can be ignored once only New UI is served;
the page hash and other query parameters remain usable.
