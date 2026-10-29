# Teams Meeting Media Bridge

For incoming one-to-one calls to explicitly mapped existing assistants, see
[Teams assistant calls](teams-calls.md). The same Windows bridge supports both paths.

This MVP keeps live Teams media on a separate Azure Windows-hosted service and keeps raw audio,
Teams credentials, Azure Speech credentials, and OpenAI Realtime credentials out of guest VMs.

## Host Integration

Configure the Python control plane with:

```yaml
teams_meeting_media_bridge_url: "https://your-bridge.example"
teams_meeting_media_bridge_token_env: "TEAMS_MEETING_MEDIA_BRIDGE_TOKEN"
teams_meeting_default_voice: "en-US-JennyNeural"
teams_meeting_poll_interval_seconds: 5
teams_meeting_request_timeout_seconds: 15
teams_meeting_max_active_meetings: 1
```

Start a meeting session from the host:

```bash
evander-cli teams-meeting start \
  --directory root \
  --join-url "https://teams.microsoft.com/l/meetup-join/..." \
  --model gpt-6-sol
```

To project an existing agent into the meeting instead of creating an unrelated meeting-only agent,
include the originating session id:

```bash
evander-cli teams-meeting start \
  --directory root \
  --join-url "https://teams.microsoft.com/l/meetup-join/..." \
  --origin-session-id existing-agent-session \
  --prompt "Represent this agent in the meeting and capture decisions."
```

The command creates a dedicated session with:

- `teams_meeting_get_events`
- `teams_meeting_speak`
- `teams_meeting_stop`

The meeting ingest worker polls the bridge and stores finalized transcript and speech-status events in `runtime/teams-meetings.json`. When the meeting is linked to an originating session, transcript events are also mirrored into `runtime/voice-sessions-store.json`. The `trigger:teams-meeting:new-transcript` trigger emits one workflow event per finalized transcript event.

The bridge client applies `teams_meeting_request_timeout_seconds` to join, event polling, speech,
and leave requests; the default is 15 seconds. A `trigger:teams-meeting:new-transcript` definition
may set `session_ids` to emit only meetings projected from those sessions. An empty list accepts
transcripts from every stored meeting visible to that trigger.

## Media Bridge Contract

- `POST /meetings/join`
- `GET /meetings/{meeting_id}/events?after=<sequence>`
- `POST /meetings/{meeting_id}/speak`
- `POST /meetings/{meeting_id}/leave`

The bridge under `integrations/teams-meeting-media-bridge` implements this contract with adapter
interfaces. `Bridge:AdapterMode` defaults to `InMemory` for local builds and tests. Set
`Bridge:AdapterMode=Graph` on the Windows bridge host to enable the production Graph media path.
Graph mode supports selectable speech backends:

- `Bridge:SpeechMode=AzureSpeech` keeps the existing Azure Speech recognition/synthesis path.
- `Bridge:SpeechMode=OpenAIRealtime` streams Teams audio through an in-process OpenAI Realtime
  WebSocket and streams Realtime model audio back to the Teams media socket.

The join request may include `projection` and `realtime` objects. OpenAI Realtime mode uses those
objects as the meeting session instructions/model overrides so a projected Teams session can speak
as the originating Evander agent instead of an unrelated meeting-only hand-off.

## Production Bridge Configuration

Required bridge process configuration for Graph mode:

```bash
TEAMS_BRIDGE_TOKEN="shared bearer token used by evander"
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

`TEAMS_MEETING_BRIDGE_TOKEN` is still accepted as a compatibility alias for the bridge bearer token.
`AZURE_SPEECH_ENDPOINT` can be used instead of `AZURE_SPEECH_REGION`. If the media host must use a
fixed media range, set both `TEAMS_BOT_MEDIA_PORT_RANGE_START` and
`TEAMS_BOT_MEDIA_PORT_RANGE_END`.

To use OpenAI Realtime instead of Azure Speech, keep the same Graph settings and replace the Azure
Speech settings with:

```bash
BRIDGE_SPEECH_MODE="OpenAIRealtime"
OPENAI_REALTIME_API_KEY="..."
OPENAI_REALTIME_MODEL="gpt-realtime-2"
OPENAI_REALTIME_BASE_URL="wss://api.openai.com/v1/realtime"
OPENAI_REALTIME_VOICE="marin"
OPENAI_REALTIME_TURN_DETECTION="semantic_vad"
OPENAI_REALTIME_TRANSCRIPTION_MODEL="gpt-live-transcribe"
```

`OPENAI_API_KEY` is accepted as a compatibility fallback when `OPENAI_REALTIME_API_KEY` is not set.
`OPENAI_REALTIME_BASE_URL` must be a `ws://` or `wss://` Realtime WebSocket URL.

Required tenant setup:

- Grant and admin-consent Microsoft Graph application permissions `Calls.AccessMedia.All`, `Calls.JoinGroupCall.All`, and `OnlineMeetings.Read.All` or the equivalent online-meeting read permission used by the lookup user.
- Add `Calls.JoinGroupCallAsGuest.All` only if the bot must join as a guest.
- Register an Azure Bot with the Teams channel enabled, calling enabled, and webhook set to `${TEAMS_BOT_PUBLIC_CALLBACK_BASE_URL}/api/calls`.
- Install the certificate matching `TEAMS_BOT_CERT_THUMBPRINT` on the Windows media host and expose the callback plus media ports publicly.

Graph mode resolves the manual Teams `join_url` through
`/users/{TEAMS_ONLINE_MEETING_LOOKUP_USER_ID}/onlineMeetings`, initializes the Microsoft
application-hosted media platform, receives Teams 16 kHz 16-bit mono PCM audio, chunks outbound audio
into 20 ms PCM frames, and signals Graph recording status before finalized media-derived transcript
text is emitted. Azure Speech mode sends that PCM to Azure Speech for STT/TTS. OpenAI Realtime mode
resamples Teams PCM to Realtime's 24 kHz PCM WebSocket input, resamples Realtime output back to
Teams 16 kHz PCM, and does not require Azure Speech credentials. Raw audio is streamed through the
bridge process and is not persisted by `evander`.

Projected OpenAI Realtime meeting sessions keep the same Graph/application-hosted media boundary:
Teams is still joined by the bridge, while the control plane stores the projection and
reconciliation state. On meeting stop, the control plane schedules a same-context wake for the
originating agent with the transcript and meeting summary.

## Manual Acceptance Runbook

Automated tests use mocked Graph/media/Speech boundaries because real-time media requires tenant registration, public callback/media reachability, certificates, and Azure Windows hosting.

1. Deploy the bridge to an Azure Windows Server with the configuration above.
2. Confirm the Azure Bot Teams calling webhook points to `/api/calls` and tenant admin consent has been applied after every permission change.
3. Start a meeting from the host with `evander-cli teams-meeting start --join-url "https://teams.microsoft.com/l/meetup-join/..."`.
4. Confirm the bot joins the scheduled meeting, finalized transcript events appear within 5-15
   seconds, `teams_meeting_speak` is audible, and `teams_meeting_stop` leaves cleanly.
5. For `Bridge:SpeechMode=OpenAIRealtime`, start with `--origin-session-id` and confirm the bridge
   emits Realtime transcript/speech-status events and the originating voice session is reconciled
   when the meeting stops.
