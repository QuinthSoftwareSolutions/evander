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

## Unified Issue Abstraction

Issue workflows now use one host-side abstraction (`local-issues.json`) regardless of origin:

- **Local issues** stay local (`source: local_gui`) and are handled entirely in host runtime state.
- **Remote-backed issues** are modeled as local proxy issues (`source: proxy_azure_devops`) with:
  - remote identity and routing data in `metadata.remote`
  - locally cached remote snapshot data in `metadata.proxy_cache`

This keeps Desktop and CLI behavior consistent: listing, selecting, and VM setup always target a
local issue record first, then optionally forward provider operations.

### Forwarding path for proxy issues

When an operator reads a proxy issue, posts a comment, or updates state/fields:

1. Admin service resolves the local issue record (number, session-style ID, or proxy ID alias).
2. The issue service maps it to `(provider, remote_issue_id)` from `metadata.remote`.
3. The provider adapter executes the remote Azure DevOps call.
4. The local proxy cache (`metadata.proxy_cache`) is refreshed from the latest remote payload.

Non-proxy records bypass forwarding and update only local runtime data.

## Why Logic Is Split Between Shell And Python

The split is about operational boundary, not language preference.

- Shell scripts own machine orchestration.
- Python modules own application and policy logic.

### Shell-first responsibilities

Use `scripts/*.sh` when the main job is coordinating host and guest environments by invoking
existing system tools. Typical examples in this repo include:

- libvirt lifecycle work such as `virt-install`, `virsh`, `qemu-img`, and `cloud-localds`
- SSH, `scp`, and filesystem copy or move steps between host and guest
- bootstrap sequencing across multiple host tools and guest commands
- systemd unit installation and other host integration glue
- thin wrappers that collect environment variables and then launch Python entry points

This repo keeps that work in shell because the code is mostly process orchestration. Shell keeps
those flows short, explicit, and easy to run manually while debugging.

### Python-first responsibilities

Use `src/agent_sandbox/*.py` when the main job is implementing product behavior rather than
invoking the operating system. Typical examples in this repo include:

- MCP gateway behavior and relay request handling
- policy checks, approval gating, and audit decisions
- typed config loading and validation
- persistent state management and worker loops
- structured provider integrations, retries, and error handling

This repo keeps that work in Python because it is easier to make structured logic testable,
maintainable, and less brittle there than in Bash.

### Mixed cases

Some shell scripts embed small Python snippets. That is intentional.

- Keep the overall flow in shell if the script still primarily orchestrates external commands.
- Use a small Python helper inline when shell would be awkward or fragile for structured parsing,
  YAML or JSON manipulation, base64 handling, or path normalization.
- If the embedded Python grows into substantial logic or starts carrying business rules, move that
  logic into a proper Python module or CLI entry point and let shell stay as the orchestration
  layer.

### Practical rule of thumb

When adding new code, ask:

- Is this mostly orchestrating the environment, host tools, or guest setup? Put it in shell.
- Is this mostly implementing long-lived service behavior, policy, state, or structured data
  processing? Put it in Python.

If both apply, keep the boundary narrow: shell should launch and sequence, Python should decide and
validate.

## Host-Driven Feedback Heartbeat (Design)

This model keeps feedback polling on the host while preserving host-side policy and secret
boundaries.

### Poll cadence and latency target

- Recommended steady-state cadence: every **2-5 minutes** per active session.
- Expected operator-to-guest feedback visibility: typically within one poll interval
  (**~2-5 minutes**), plus MCP round-trip time (normally seconds).
- Faster cadences are possible but increase gateway/provider load; slower cadences reduce load but
  increase human feedback latency.

### Required MCP calls

Per heartbeat loop, the host worker performs provider polling for streams allowlisted in active session manifests and updates the shared feedback store.

Guests continue to use MCP tools (`has_pending_feedback`, `list_unread_feedback`, `list_all_feedback`, `post_pr_feedback_reply`) for consumption and reply actions.

This sequence minimizes payload volume when there is no new feedback and supports explicit at-least-once processing semantics.

### State model (unread cursor per session)

- The host keeps provider cursors per feedback stream in host runtime state.
- `list_unread_feedback` returns:
  - ordered feedback items after the provided cursor
  - a `next_cursor` watermark
- Cursor advancement rule:
  - advance only after feedback is successfully persisted/processed
  - optionally coupled with explicit host acknowledgment/update call
- Sessions are isolated: cursor `A` for session `X` is never reused for session `Y`.

### Failure behavior

- **Provider API unavailable:** keep current cursor, log failure, retry next cadence with bounded backoff/jitter; do not skip forward.
- **Stale/invalid cursor:** host returns cursor error; guest falls back to a safe recovery mode
  (for example reset to latest checkpoint or request replay from a host-defined baseline), then
  resumes normal polling.
- **Partial fetch/processing failure:** treat batch as uncommitted; retain prior cursor and
  re-fetch on next poll so unread entries are not lost.
- **Duplicate delivery tolerance:** guest handlers should be idempotent by feedback ID because
  at-least-once replay can occur during failures.

### Security boundary

- Provider credentials (Discord, email, OpenAI, etc.) remain **host-side only**.
- Guest has access only to MCP tool interfaces over the private VM network.
- The host heartbeat loop exchanges only policy-filtered feedback records and cursor metadata, never upstream provider secrets.

## Planned Next Integrations

- Discord integration now uses a provider-backed bot API adapter (`discord_backend: bot_api`) while
  preserving the same MCP tool contract.
- Email integration now uses provider-backed Gmail and Microsoft Graph adapters, with the JSON
  store retained only as an optional development fallback while preserving the same MCP tool
  contract.
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
