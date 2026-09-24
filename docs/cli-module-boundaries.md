# CLI module boundaries

`evander.cli:app` remains the console entry point, and `python -m evander.cli`
uses the same application. `evander.cli` statically imports and re-exports
command functions and compatibility helpers.

`cli_app` creates the Typer applications, nested command groups, and help panel
ordering. Domain modules register their commands on those applications using
the usual decorators. Import `evander.cli` when the complete command tree is
needed; command modules register their groups as they are imported.

| Responsibility | Modules |
| --- | --- |
| Runtime configuration and admin/runtime assembly | `cli_runtime` |
| Common option types, output, and subprocess helpers | `cli_common` |
| Health and state maintenance | `cli_health`, `cli_maintenance` |
| Directory configuration, connectors, setup, and managed Git | `cli_directories`, `cli_connectors`, `cli_setup`, `cli_git` |
| Sessions, policy/identity helpers, and backend migration | `cli_sessions`, `cli_session_support`, `cli_session_migration` |
| VMs, issues, and execution backends | `cli_vm`, `cli_issues`, `cli_execution` |
| Team commands and collaboration integrations | `cli_team`, `cli_auth`, `cli_meetings` |
| Scheduled wakes, workers, and fixtures | `cli_wake`, `cli_workers`, `cli_seed` |

Shared helpers are called through their owning module, so command dependencies
remain explicit. Tests that replace runtime construction should patch
`evander.cli_runtime.build_admin_runtime`; other replacements should target the
module implementing the operation. Compatibility imports remain aliases of
the actual command functions, rather than dispatch wrappers.

Keep command names, decorators, option aliases, defaults, help text, and exit
handling in the command module. The grouped help ordering remains defined in
`cli_app.GroupedHelpTyperGroup`. CLI modules stay beside `cli.py`, preserving the
repository-relative script paths used by VM commands.
