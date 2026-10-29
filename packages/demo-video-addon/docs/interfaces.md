# Demo video interfaces

## Host narration

Register add-on ID `demo-video-addon`, module `evander_demo_video_addon`, connector driver
`demo_video_narration`. The only operation is `synthesize`, with write access and an artifact
result. Input is exactly `{"text":"..."}`, 1–2000 characters. Existing connector operation
grants, argument policies and configured approvals apply. No new MCP tool is introduced.

The host configuration fixes `base_url`, `model`, `voice`, optional `instructions` and `speed`.
Authentication is an existing connector auth profile. The adapter sends a bounded
`POST <base_url>/audio/speech` request with WAV output. HTTPS is required; loopback HTTP is
accepted for local tests. Redirects are not followed. The maximum is 40 seconds, 8 MB and
120 seconds of decoded PCM per beat. Authorization is rechecked during delivery and before
staging. Failed, revoked, malformed, truncated or oversized responses produce no audio artifact.

The result is the normal `ConnectorInvocationResult`: a session-owned `audio/wav` artifact,
its digest/expiry and `value.duration_seconds`. No host path, credentials or raw audio is
returned through the connector result. Copy it into the guest with
`communication_copy_artifact_to_workspace`, then import it into the request workspace.

Speech-provider migration is deliberately separate. Keep configuration consistent with the
current voice setup. Do not move existing browser voice users as part of this feature.

## Portable worker

The shipped `worker.py` has only Python standard-library dependencies and runs inside the
existing session. Use a separate directory for each immutable PR feedback request. Its
`manifest.json` contract and agent procedures are in the packaged
[demo-video skill](../evander_demo_video_addon/skills/demo-video/SKILL.md).

Version-1 requests without `kind`, or with `kind: "pr"`, retain the original PR contract.
For an existing Teams chat, `kind: "teams"` requires the exact `channel_target`, original
`message_id`, and `title`; `summary`, `target`, and `source_reference` are optional. A
cards-only request needs no target. The separate [general-video skill](../evander_demo_video_addon/skills/general-video/SKILL.md)
describes the Teams procedure; the worker never invents PR or build metadata for it.

| Command | Behavior |
|---|---|
| `preflight --toolchain <root>` | Check encoders, filters, font and ffprobe. |
| `preflight ... --audio <wav> --recording <webm> --request-key <key> --receipt <path>` | Also check actual narration/capture and write `ready:<key>` only after success. |
| `import-audio --manifest <path> --scene <id> --source <copied-wav>` | Copy a completed WAV into the request and bind its digest to the exact narration text. |
| `render --manifest <path> --toolchain <root>` | Render/cache beats, join the MP4, decode/validate it, and write a transcript and measured timeline. |
| `verify --manifest <path> --toolchain <root>` | Check the final MP4's tracks, duration, dimensions, size and decodability. |
| `stage-receipt --manifest <path> --kind video\|transcript\|captions --artifact-id <id>` | Record a successful existing staging tool's receipt, bound to the current file digest. Captions are optional. |
| `publication --manifest <path>` | Return stable final reply arguments, or the already published reply. |
| `published --manifest <path> --reply-id <id>` | Save the successful PR reply receipt; `--message-id` is an equivalent alias for a Teams post. |
| `install --archive <bundle> --root <root>` | Validate/extract an offline media bundle and atomically select its private `current` directory. |

Media inputs are regular, relative paths inside the request workspace, with no symlink or
parent traversal. The worker never interprets manifest values as shell commands. Optional beat
captions use a separate plain-text SRT file. A changed script requires newly imported speech; modified media
invalidates its render/staging receipts. Published requests are immutable.

The final MP4 is 1280×720 at 24 fps, H.264/yuv420p with mono 48 kHz AAC. Video targets 1 Mbit/s,
audio 128 kbit/s. Each scene keeps its actual browser-action speed. A short beat is extended
on useful still frames when narration is longer; shorter narration is followed by silence
while the remaining action finishes. Keep each beat's words aligned with its action/result,
and split a beat if the association is unclear. A single narration track over arbitrary
exploratory footage is not the production workflow.

Captions are not burned into the picture or automatically shown. The worker writes optional
`captions.srt` from the same measured timeline, for loading in a compatible player. Publish the
MP4 and transcript by default; if captions are requested, stage the SRT and save its
`--kind captions` receipt before generating the final publication arguments. Some provider
previews cannot load an external SRT; a downloaded video in a compatible player can.

Maximum output duration is five minutes and maximum size is 50 MiB. Caption and card density
checks ask the agent to split an overlong beat instead of creating unreadable output. The
worker adds a visible AI-narration label, and the transcript identifies PR/build/source and
labels evidence as `observed`, `source` or `illustration`. Agent visual/audio review remains
necessary; format validation alone cannot establish that the explanation is useful or true.
For Teams requests the transcript includes only real supplied source or target information.
`publication` returns the original chat target, staged artifact IDs, text and a stable key
derived from chat and incoming message IDs. Use them unchanged with `teams_post_file`, with
no `thread_id` for a chat. If the outcome is uncertain, retry only the same payload and key;
an uncertainty or payload conflict requires operator reconciliation, not a new key.

## Browser capture

`capture.mjs` exports:

- `preflightCapture(chromium)`: record an empty local page using the seeded helper; return
  `success` and the completed recording path. It never visits the target app.
- `createRecorder({chromium, jobDirectory})`: return `recordScene(...)` and `close()`.
- `recordScene({id, targetAlias, setup, act, verify})`: create a managed website context,
  establish the starting view, record the action and assert its result. Callbacks receive
  a Playwright page; action/verification callbacks also receive `mark(label)`.

An approved unauthenticated target uses `createRecorder({chromium, jobDirectory,
allowPublic: true})` and `recordScene` with `access: "public"`. This requires the
session's host-owned browser proxy and disables Chromium's implicit loopback proxy bypass.
The proxy remains responsible for restricting destinations. Managed access is the default;
an authentication failure never falls back to public access. Changing a scene's access mode
or target requires a new scene ID, while old receipts without an access field remain managed.

The recorder stores a completed clip, trim boundaries, action/result markers and a digest.
It finishes setup before recording and starts its clock on the first received video frame.
The seeded Playwright runtime must support `page.screencast.start` with `path` and `onFrame`;
the empty-page preflight checks this before deployment. A missing first frame times out after ten seconds.
The managed context and seeded Chromium preserve the existing origin/proxy/authentication
rules. No auth state is exported. Completion is awaited before saving the recording.

Completed scene IDs are reusable. If a scene failed or was interrupted, its action outcome
is unknown and the helper refuses an automatic replay. The agent must inspect application
state before choosing a new scene ID. This is a per-request checkpoint, not an environment
reservation or lease.
