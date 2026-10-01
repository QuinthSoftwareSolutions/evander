# Calling an existing assistant through Teams

Native calls use the existing Azure Windows Teams media bridge. One calling bot serves
desktop and web users; root configuration explicitly maps each tenant/user pair to an
existing assistant session. Calling starts disabled. Text workflows keep their existing
bindings and credentials.

## Configure the caller map

Use Admin Web **Configuration → root → People & Communications → Teams → Call Routes**
(`teams_call_routes`). If **Unset** is checked, uncheck it, then select **Add caller route**.
Enter the tenant GUID and the caller's Entra object GUID, and choose an assistant. Names
and session IDs appear together; choosing a session also sets its directory. Save with
the current revision.
The equivalent provider fields are:

```json
{
  "teams_call_enabled": false,
  "teams_call_max_concurrency": 4,
  "teams_call_routes": [{
    "tenant_id": "11111111-1111-4111-8111-111111111111",
    "caller_user_id": "22222222-2222-4222-8222-222222222222",
    "directory_path": "root/personal",
    "origin_session_id": "assistant-example"
  }]
}
```

These synthetic values describe the shape, not an activation configuration. Keep actual
operator copies, identities, and secrets outside the repository. Routing and concurrency
are root-only. `teams_call_enabled` can be overridden to false in an assistant directory;
root must also enable admission. Duplicate tenant/user keys are rejected. No route means
no call. Replacing an assistant session requires explicitly updating its route.

The selected assistant must separately permit the caller through its effective Teams user
permissions, including communication-target policy where configured. Multiple assistants
can permit the same user. Permissions never select the assistant. Existing email references
are resolved through Graph for authorization; the route itself always uses object IDs.
Set the Teams provider tenant to the actual tenant GUID, not `common`. A mapping cannot
override the provider tenant or move a session into a different directory.

Grant `submit_voice_query_response` without approval to each call-enabled assistant through
the supported session administration surface. Also add it to the `allowed_tools` in the
workflow's assistant creation node, using workflow validation and revision-checked saving.
Leave ordinary action approvals intact. For recaps, retain `teams_post_status` and the
existing direct-chat permissions. New assistants require their own explicit route.

## Set up the bridge host

Use a supported Azure Windows Server x64 host with the .NET 10 runtime, a media certificate
with a private key in `LocalMachine/My`, public callback HTTPS, and Graph media connectivity.
Provision the public IP, callback hostname, certificate, and configured media ports according
to [Microsoft's application-hosted media requirements](https://learn.microsoft.com/en-us/microsoftteams/platform/bots/calls-and-meetings/requirements-considerations-application-hosted-media-bots).
No additional guest firewall ports are needed. The bridge needs authenticated connectivity
to the control plane; keep the gateway behind the established private network or HTTPS ingress.

Register an Entra app and Azure Bot with the Teams channel and calling enabled. Point its
calling webhook at `https://<bridge>/api/calls`. Obtain tenant admin consent for
`Calls.AccessMedia.All` and `Calls.JoinGroupCall.All`; delegated text-chat permissions are
separate. Notifications use communications signing keys, audience equal to the bot app ID,
and a required matching tenant claim. [Microsoft's call-answer permissions](https://learn.microsoft.com/en-us/graph/api/call-answer?view=graph-rest-1.0).

Copy [the app manifest template](../integrations/teams-meeting-media-bridge/teams-app/manifest.json)
into operator storage, replace IDs, domains and organization URLs, and add your required
32×32 transparent outline and 192×192 color PNG icons. Zip the manifest and both icons at
the archive root, publish through your organization's Teams app administration, and install
the personal app for pilot users. Video, group calls, outbound calling, and mobile bot calling
are outside this implementation.

Copy [bridge.env.example](../integrations/teams-meeting-media-bridge/bridge.env.example) into
protected operator storage. Set Graph media options and a bridge-local realtime API key.
`TEAMS_BRIDGE_TOKEN` authenticates control-plane polls; the root provider setting
`teams_meeting_media_bridge_token_env` must name an environment variable containing the
same value. Set `teams_meeting_media_bridge_url` to the bridge's authenticated HTTPS URL.

`VOICE_MEDIA_CONTROLLER_URL` on the bridge points at the control plane, while
`VOICE_MEDIA_CONTROLLER_TOKEN` matches the control plane's root
`voice_media_controller_token_env`. Assistant directories must inherit that credential.
The realtime model comes from the selected directory's existing `openai_realtime_model`;
the voice defaults to `marin`. The bridge keeps model credentials locally.

Configure Kestrel's HTTPS certificate separately from the Graph media certificate, or use
an authenticated HTTPS reverse proxy. Keep the internal HTTP listener private. For
user-operated foreground execution, load the protected environment and run:

```powershell
dotnet run --project integrations/teams-meeting-media-bridge/TeamsMeetingMediaBridge
```

For service installation or an update on a stopped bridge, run from an elevated x64 PowerShell:

```powershell
./integrations/teams-meeting-media-bridge/install-windows-service.ps1 `
  -InstallDirectory C:\Services\EvanderTeamsBridge `
  -EnvironmentFile C:\OperatorConfig\teams-bridge.env -Preflight
./integrations/teams-meeting-media-bridge/install-windows-service.ps1 `
  -InstallDirectory C:\Services\EvanderTeamsBridge `
  -EnvironmentFile C:\OperatorConfig\teams-bridge.env
```

The installer publishes locally, protects its directory and service environment, and
registers `EvanderTeamsMediaBridge`. It does not start it. Drain calls before updating;
calls are pinned to this host. Start it explicitly once networking and consent are ready.
The media SDK is pinned to `1.2.0.18736`; keep it within Microsoft's supported update window.

## Call lifecycle and recovery

The bridge validates caller identity, asks `POST /voice-sessions/inbound-teams-calls` for a
binding within five seconds, and answers within the remaining admission deadline. The
endpoint accepts tenant ID, caller ID, and Graph call ID; it rejects caller-selected session
IDs. Retries reuse the durable binding. A route edit affects the next call, not an active call.
Reservations permit one call per assistant and four globally by default.

The companion exposes only `query_origin_agent`. It delegates into the assistant's existing
context and polls the durable query every second, with a 90-second query deadline. Replayed
function events and retried query requests do not create duplicate work. Original tools,
approvals, and prompt serialization still apply. Interrupting speech clears pending audio;
hangup cancels voice polling while already accepted assistant work can continue.

Transcript collection starts only after Graph accepts recording status. Raw audio files
are not created. Durable transcripts use the existing voice privacy datasets; configure
retention through [Privacy operations](privacy-operations.md).
Bridge terminal snapshots expire from memory after one hour; the durable control plane
remains authoritative. Protect `GET /calls/{voice_session_id}` with the bridge token.

Enable the existing `evander-teams-meeting-ingest` supervisor for the intended directories
with `poll_interval_seconds: 5` and `jitter_seconds: 0`. Restart the supervisor after changing
its interval, bridge URL, or credential variable name so it loads the new settings.
Direct completion updates schedule
reconciliation immediately; polls recover delivery failures and bridge restarts. A missing
bridge call becomes failed after 30 seconds without a successful poll; other bridge
communication failures get a 90-second recovery window. Terminal records release capacity
and schedule one reconciliation in the origin context. The assistant is instructed to
retain completed work and send one concise recap in the existing Teams chat for substantive
calls; otherwise it updates memory silently.

## Validate and activate

First verify local suites with explicit timeouts:

```bash
timeout 180 ./.venv/bin/pytest -q tests/test_teams_voice_service.py tests/test_voice_sessions.py tests/test_teams_meeting.py tests/test_gateway.py
timeout 120 dotnet run --project integrations/teams-meeting-media-bridge/TeamsMeetingMediaBridge.Tests
```

On the prepared Windows host, enable `TEAMS_CALLS_ENABLED=true`, start the bridge, and enable
one root mapping through the revision-checked configuration UI. Confirm two-way audio,
a tool-backed answer, interruption, preserved assistant context, and a substantive recap.
Then test two concurrent callers mapped to separate assistants. Also give a caller access
to two assistants and verify its explicit route still selects the intended one.

Review admission logs, answer latency, delegated query latency, active durable call records,
and reconciliation failures before adding more reviewed mappings. Rollback disables new
admissions using root `teams_call_enabled=false`; keep supervision and accepted work running.
Local fake tests do not substitute for this Windows media acceptance check.
