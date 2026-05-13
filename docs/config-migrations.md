# Configuration Migrations

## Retell Phonebook To Directory Contacts

Retell-specific `retell_phonebook` entries in `providers.json` are retired. Move each old
phonebook alias into directory-scoped `contacts.json`.

Before:

```json
{
  "retell_phonebook": {
    "ops_duty": {
      "display_name": "Ops Duty",
      "phone_number": "+15551230001",
      "agent_id": "agent_old"
    }
  }
}
```

After:

```json
{
  "contacts": {
    "ops_duty": {
      "name": "Ops Duty",
      "description": "",
      "email_addresses": [],
      "phone_numbers": [
        {"label": "primary", "number": "+15551230001", "primary": true}
      ]
    }
  }
}
```

Do not carry over per-contact `agent_id`; Retell now uses `retell_default_agent_id`.

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
