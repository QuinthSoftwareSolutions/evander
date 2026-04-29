# Teams Meeting Media Bridge

This MVP keeps live Teams media on a separate Azure Windows-hosted service and keeps raw audio, Teams credentials, and Azure Speech credentials out of guest VMs.

## Host Integration

Configure the Python control plane with:

```yaml
teams_meeting_media_bridge_url: "https://your-bridge.example"
teams_meeting_media_bridge_token_env: "TEAMS_MEETING_MEDIA_BRIDGE_TOKEN"
teams_meeting_default_voice: "en-US-JennyNeural"
teams_meeting_poll_interval_seconds: 5
teams_meeting_max_active_meetings: 1
```

Start a meeting session from the host:

```bash
agent-sandbox-cli teams-meeting start \
  --directory root \
  --join-url "https://teams.microsoft.com/l/meetup-join/..." \
  --model gpt-5.4
```

The command creates a dedicated session with:

- `teams_meeting_get_events`
- `teams_meeting_speak`
- `teams_meeting_stop`

The meeting ingest worker polls the bridge and stores finalized transcript and speech-status events in `runtime/teams-meetings.json`. The `trigger:teams-meeting:new-transcript` trigger emits one workflow event per finalized transcript event.

## Media Bridge Contract

- `POST /meetings/join`
- `GET /meetings/{meeting_id}/events?after=<sequence>`
- `POST /meetings/{meeting_id}/speak`
- `POST /meetings/{meeting_id}/leave`

The bridge under `integrations/teams-meeting-media-bridge` implements this contract with adapter interfaces. `Bridge:AdapterMode` defaults to `InMemory` for local builds and tests. Set `Bridge:AdapterMode=Graph` on the Windows bridge host to enable the production Graph/Azure Speech adapter path.

## Production Bridge Configuration

Required bridge process configuration for Graph mode:

```bash
TEAMS_BRIDGE_TOKEN="shared bearer token used by agent-sandbox"
BRIDGE_ADAPTER_MODE="Graph"
TEAMS_BOT_APP_ID="00000000-0000-0000-0000-000000000000"
TEAMS_BOT_APP_SECRET="..."
TEAMS_BOT_TENANT_ID="00000000-0000-0000-0000-000000000000"
TEAMS_BOT_PUBLIC_CALLBACK_BASE_URL="https://bridge.example.com"
TEAMS_BOT_SERVICE_FQDN="bridge.example.com"
TEAMS_BOT_PUBLIC_IP="203.0.113.10"
TEAMS_BOT_PUBLIC_PORT="8445"
TEAMS_BOT_INTERNAL_PORT="8445"
TEAMS_BOT_CERT_THUMBPRINT="..."
TEAMS_ONLINE_MEETING_LOOKUP_USER_ID="meeting-lookup-user@tenant.example"
AZURE_SPEECH_KEY="..."
AZURE_SPEECH_REGION="westeurope"
AZURE_SPEECH_RECOGNITION_LANGUAGE="en-US"
AZURE_SPEECH_DEFAULT_VOICE="en-US-JennyNeural"
```

`TEAMS_MEETING_BRIDGE_TOKEN` is still accepted as a compatibility alias for the bridge bearer token. `AZURE_SPEECH_ENDPOINT` can be used instead of `AZURE_SPEECH_REGION`. If the media host must use a fixed media range, set both `TEAMS_BOT_MEDIA_PORT_RANGE_START` and `TEAMS_BOT_MEDIA_PORT_RANGE_END`.

Required tenant setup:

- Grant and admin-consent Microsoft Graph application permissions `Calls.AccessMedia.All`, `Calls.JoinGroupCall.All`, and `OnlineMeetings.Read.All` or the equivalent online-meeting read permission used by the lookup user.
- Add `Calls.JoinGroupCallAsGuest.All` only if the bot must join as a guest.
- Register an Azure Bot with the Teams channel enabled, calling enabled, and webhook set to `${TEAMS_BOT_PUBLIC_CALLBACK_BASE_URL}/api/calls`.
- Install the certificate matching `TEAMS_BOT_CERT_THUMBPRINT` on the Windows media host and expose the callback plus media ports publicly.

Graph mode resolves the manual Teams `join_url` through `/users/{TEAMS_ONLINE_MEETING_LOOKUP_USER_ID}/onlineMeetings`, initializes the Microsoft application-hosted media platform, uses Azure Speech for 16 kHz 16-bit mono PCM speech recognition/synthesis, chunks outbound TTS into 20 ms PCM frames, and signals Graph recording status before finalized media-derived transcript text is emitted. Raw audio is streamed through the bridge process and is not persisted by `agent-sandbox`.

## Manual Acceptance Runbook

Automated tests use mocked Graph/media/Speech boundaries because real-time media requires tenant registration, public callback/media reachability, certificates, and Azure Windows hosting.

1. Deploy the bridge to an Azure Windows Server with the configuration above.
2. Confirm the Azure Bot Teams calling webhook points to `/api/calls` and tenant admin consent has been applied after every permission change.
3. Start a meeting from the host with `agent-sandbox-cli teams-meeting start --join-url "https://teams.microsoft.com/l/meetup-join/..."`.
4. Confirm the bot joins the scheduled meeting, finalized transcript events appear within 5-15 seconds, `teams_meeting_speak` is audible, and `teams_meeting_stop` leaves cleanly.
