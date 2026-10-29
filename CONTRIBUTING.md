# Contributing

Issues and pull requests are welcome. For a substantial change, open an issue first to
discuss the problem and approach. For vulnerabilities, use the private reporting process in
[SECURITY.md](SECURITY.md), not a public issue or pull request.

## Development setup

Use Python 3.12 or newer, [uv](https://docs.astral.sh/uv/), and Node.js 24 for Admin Web.
From the repository root, install the locked Python development dependencies and run the
complete suite:

```bash
uv sync --frozen --extra dev
./.venv/bin/python scripts/run_tests.py
```

For Admin Web, install its dependencies and run the build and default tests:

```bash
cd frontend/admin-web
npm ci
npx playwright install --with-deps chromium
npm run build
timeout --kill-after=10s 600s npm test
```

See [the testing guide](docs/testing.md) for focused commands, add-on coverage, browser
requirements, and optional smoke tests. Tests requiring local infrastructure may need
additional setup; note any skipped or unavailable checks in the pull request.

## Pull requests

Keep changes focused, describe the problem and resulting behavior, and include tests for
meaningful behavior changes. Update relevant documentation and examples. Each feature or
fix branch needs one version bump and a matching [changelog](CHANGELOG.md) entry; keep
`pyproject.toml`, `src/evander/__init__.py`, and `uv.lock` in sync. Use a patch bump for
fixes and maintenance and a minor bump for new user-visible functionality.

Do not commit credentials, runtime state, customer-specific data, or private incident
material. Use synthetic examples and fixtures. Contributions are distributed under the
repository's [license](LICENSE); preserve notices for third-party material.
