---
name: general-video
description: Create a narrated explainer from explanatory cards and approved browser recordings, then deliver the video and transcript to the requesting Teams chat.
---

# General video

Make a clear, evidence-backed explainer for the person who requested it. Explain the purpose,
the important steps and their practical meaning. Use short readable cards and, when helpful,
record approved browser targets. Distinguish things actually observed, explanations supported
by a source, and illustrations. Do not present imagined behavior as an observation.

Aim for two to three minutes and never exceed five. Use the user's preferred language and the
configured narration voice. Deliver an MP4 and a plain-text transcript; provide the separate
SRT only when requested. The video and transcript disclose AI-generated narration.

## Request identity and boundaries

Use the originating `chat:` channel target and message ID supplied by the host for the video
request. Both are required; never guess them from user text, substitute a user target, or use a
later steering message as the identity of an existing request. If either is unavailable, stop
and explain the missing information. Use that exact chat for every publication attempt.

Keep media outside application source in a private per-request directory under
`/home/agent/demo-video`. Derive its name from the SHA-256 of the UTF-8 JSON object containing
`channel_target` and `message_id`, serialized with sorted keys. Reuse this directory for
recovery. Follow-up instructions may refine an unpublished request without replacing its
identity. Once published, a new video requires a new user message and request directory.

Use only the assistant's existing approved sources, files and website targets. Never print or
persist credentials, cookies or browser state. Do not deploy, reset a database, alter product
source or push commits. Preserve unrelated data; perform application changes only when explicitly
requested and already allowed. Coordinate any use of a shared application with its users.

## Readiness

The reviewed toolchain is `/home/agent/.evander/demo-video/current`. Run `worker.py` with
Python 3 and import `capture.mjs` from Node. Use the seeded Playwright runtime and launch
helper. Set `PLAYWRIGHT_BROWSERS_PATH` to `current/playwright-cache` before importing
Playwright. Do not download packages or call the speech provider directly.

Before recording, call the granted `demo-narration.synthesize` connector operation with
`{"text":"Narration is ready."}` and copy its WAV artifact into this workspace with
`communication_copy_artifact_to_workspace`. Use `preflightCapture(chromium)` on an empty local
page with `DEMO_VIDEO_PREFLIGHT_DIR` set to a disposable directory. Run
`python3 current/worker.py preflight --toolchain current --audio <wav> --recording <webm>`
with absolute paths. If a grant, approval, credential, recorder or encoder is unavailable,
stop and report the specific problem; do not claim the video is ready.

## Prepare and render

Plan and rehearse the explanation. Verify calculations independently. A cards-only explainer
needs no browser target. For a browser scene use an existing approved target alias and
`createRecorder({chromium, jobDirectory})`, followed by
`recordScene({id, targetAlias, setup, act, verify})` for a managed login. For an approved
unauthenticated target, explicitly use `createRecorder({chromium, jobDirectory,
allowPublic: true})` and `recordScene` with `access: "public"`. Public recording requires
the session's host-owned target proxy. Never switch to public access when managed login fails.
Always close the recorder in `finally`.

Create `manifest.json` in the request directory with the following contract. Replace all
example values with the host-provided identity and truthful content; `target` and
`source_reference` are optional, and no PR or build fields are required.

```json
{
  "schema_version": 1,
  "request": {
    "kind": "teams",
    "channel_target": "chat:example-chat",
    "message_id": "example-message",
    "title": "How the process works",
    "summary": "Show the main steps and explain their purpose."
  },
  "limitations": "The example uses illustrative data.",
  "scenes": [{
    "id": "purpose", "kind": "card", "title": "Why it matters",
    "body": "A short, concrete explanation.",
    "narration": "A short sentence explaining the practical benefit.",
    "evidence": [{"kind": "illustration", "reference": "Illustrative example"}]
  }]
}
```

Each browser scene records its clip, trim boundaries, action/result markers and digest.
Use the returned values in the scene and label its evidence `observed`; cite source-backed
claims with `source` and hypothetical examples with `illustration`. Completed scenes can be
reused. If an action's result is uncertain, inspect the application before choosing a new
scene ID; never automatically repeat that action.

For each short narration beat, call `demo-narration.synthesize`, copy the WAV artifact into
the workspace, and run `worker.py import-audio --manifest <manifest> --scene <id> --source
<wav>`. Reuse successful audio only when its text is unchanged. Run `worker.py render
--manifest <manifest> --toolchain current`, inspect representative frames and listen to the
narration, then run `worker.py verify --manifest <manifest> --toolchain current`. Fix gaps
before publication and report any remaining limitations.

## Deliver and recover

Stage the verified MP4 and transcript with `communication_stage_workspace_file`. Respect
existing file approvals and record each successful artifact using `worker.py stage-receipt`.
Stage optional captions only when requested. Run `worker.py publication --manifest <manifest>`;
if already published, return the existing result. Otherwise call `teams_post_file` with its
exact `channel_target`, `body_text` as `message`, `artifact_ids`, and `idempotency_key`;
omit `thread_id` for a chat. Only after a successful post, run `worker.py published --manifest
<manifest> --message-id <returned message_id>`.

If a post's outcome is uncertain, retry only the exact same payload with the same idempotency
key. If the tool reports an uncertain outcome or a conflicting payload, stop for operator
reconciliation; never send again with a fresh key. Preserve completed media, staging and
publication receipts so recovery does not redo successful work.
