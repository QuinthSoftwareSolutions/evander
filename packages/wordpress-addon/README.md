# WordPress Add-on

This optional Evander add-on publishes and recovers WordPress content, browser
edits and managed Git source changes. It requires Evander 0.76.0 or later.
The companion plugin, browser runner, UI, documentation, examples, tests and
fixtures are owned by this project. Installing Evander alone does not install it.

See [installation and operations](docs/operations.md) for credential bindings,
recovery verification, site enablement and publication grants.

## Install

From an Evander checkout, install this project into the control-plane environment:

```bash
uv pip install --python .venv/bin/python ./packages/wordpress-addon
npm ci --prefix packages/wordpress-addon
```

Seed Chromium on the host and configure `PLAYWRIGHT_MCP_EXECUTABLE_PATH` when the
browser is outside Playwright's cache. Set `EVANDER_WORDPRESS_PLAYWRIGHT_MODULE` to
the absolute `packages/wordpress-addon/node_modules/playwright/index.mjs` path for
an installed wheel. A source-loaded add-on finds that project-local runtime by
default. The browser runtime does not depend on Admin Web's Node packages.

The default add-on loader discovers this checkout's `packages/` projects without
enabling them. If `addon_loader.package_dirs` is explicitly configured, add
`packages` while preserving existing paths. For a wheel-only deployment, add `evander_wordpress_addon` to
`addon_loader.modules` in host and trigger-worker configuration, preserving
existing entries and enabled IDs. Alternatively use the existing entry-point
loader with an explicit enabled-ID allowlist. Then enable **WordPress Add-on**
through Admin Web and restart the listed services. The operations guide includes
the explicit HTTP relay credential-key setup required for browser editing.

## Develop and test

From the repository root:

```bash
uv pip install -e '.[dev]' -e './packages/wordpress-addon[dev]'
timeout --kill-after=5s 120s .venv/bin/pytest -q packages/wordpress-addon/tests
```

The repository's normal pytest collection and complete test runner also include
these tests. From this project directory, `pytest -q` runs the add-on suite against
the Evander installed in the active environment.

Real browser and Docker checks remain opt-in. Their setup and bounded commands
are documented in the [verification guide](docs/operations.md#validation-and-troubleshooting).

Build this distribution independently of Evander:

```bash
uv build --out-dir packages/wordpress-addon/dist packages/wordpress-addon
```

The wheel contains only the runtime package and its assets. The source
distribution also includes documentation, examples, tests and fixtures.
