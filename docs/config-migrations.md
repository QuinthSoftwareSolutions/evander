# Configuration Migrations

## Trigger/Effect Orchestrator Core Config Split

As of this change, `config/trigger-orchestrator/config.json` is interpreted as:

- **`OrchestrationCoreConfig`** for engine runtime concerns:
  - queue/store paths
  - poll/jitter/lock behavior
  - workflow pack references and inline IR
  - graph declarations (`triggers`, `gates`, `effects`, `queries`, `edges`)
  - engine safety limits (`max_graph_depth`, `max_events_per_cycle`)
- **`provider_loader`** for provider plugin discovery:
  - `provider_profile`
  - `include_builtin`
  - `provider_modules`
  - `provider_entry_points_enabled`

### Required migration

Older config files that still place:

- `provider_modules`
- `provider_entry_points_enabled`

at the top level are no longer accepted. Move them under `provider_loader`.

Before:

```json
{
  "provider_modules": ["my_plugin.providers"],
  "provider_entry_points_enabled": true
}
```

After:

```json
{
  "provider_loader": {
    "provider_modules": ["my_plugin.providers"],
    "provider_entry_points_enabled": true
  }
}
```
