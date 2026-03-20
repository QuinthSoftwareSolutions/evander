# Architecture Notes

## Components

- Host orchestrator: creates session manifests, launches VMs, and destroys them at the end of a run.
- MCP gateway: the only host service reachable from guests.
- OpenAI relay: a separate host service that exposes an OpenAI-compatible `/v1` endpoint to guests while keeping the real API key on the host.
- NuGet relay: a separate host service that exposes guest-reachable feed endpoints for nuget.org and private Azure Artifacts while keeping private feed credentials on the host.
- Local bare Git remote: a host-side code transfer path that lets guests push commits back over the private network.
- Disposable guests: run agent code and call the MCP gateway over the isolated libvirt network.
- Audit store: append-only JSONL log for every privileged tool attempt.

## Why This Shape

- `KVM + libvirt` is the practical default for Ubuntu 24.04.
- Guests have no direct internet path, which makes MCP the explicit capability boundary.
- Guests use the host relay for model inference instead of holding cloud API credentials directly.
- Guests use host relays for package and model access instead of holding upstream credentials directly.
- Git handles code transport; MCP remains the control and policy path.
- Session manifests are the smallest useful policy primitive for v1.
- Approval records let you keep autonomy for drafts and read operations while gating sends.

## Planned Next Integrations

- Replace the stub email service with a provider-backed adapter that preserves the same tool contract.
- Discord integration now uses a provider-backed bot API adapter (`discord_backend: bot_api`) while
  preserving the same MCP tool contract.
- Trust boundary: guests can call MCP tools only; host-side gateway is the only component that
  holds Discord credentials, calls Discord HTTPS APIs, and enforces channel/user allowlists before
  commands reach guest automation.
- Discord ingestion boundary in `bot_api` mode:
  - Poll targets are the channel IDs already present in the Discord command store
    (`discord_command_store_path`), not all reachable guild channels.
  - The gateway rejects command envelopes unless metadata marks them as
    `source = discord_bot_api` with non-empty `channel_id` and `author_id`.
  - Acceptance is `(session channel allowlist) OR (global operator user allowlist)`.
  - Post destinations remain session-channel-allowlist only.
  - There is no implicit "poll everywhere" or unrestricted history-derived source expansion.
- Failure/audit model for Discord commands:
  - Policy failures are logged as denied `tool_call` audit events in `audit_log_path`.
  - `discord_get_commands` audits each denied envelope and records aggregate `denied_count`.
  - Transport/API failures from Discord provider calls are returned to callers; no built-in retry
    backoff loop is currently implemented in the adapter.
- Add a separate admin channel for approving requests without granting the guest broader power.
