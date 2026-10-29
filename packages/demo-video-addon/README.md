# Demo Video Add-on

Create narrated PR explanations from recorded browser behavior and simple explanatory cards.
`!EXPLAIN <target>` uses an existing review session and an existing matching build. The video
explains the practical purpose of the change, including backend behavior. When usable UI is
available, the video follows real actions and visible results, with concise supporting cards.
The versioned [demo-video skill](evander_demo_video_addon/skills/demo-video/SKILL.md) owns
presentation guidance; the workflow supplies request context. English videos aim for two to
three minutes and allow up to five.
The MP4 plays with a clear picture and narration. Captions are an optional separate SRT file;
the plain-text transcript is always delivered.

The separate `general-video` skill creates narrated explainers for an existing Teams chat,
including cards-only videos and recordings of explicitly approved targets. It uses the same
media tools and preserves the originating chat and message identity for delivery. It does not
deploy applications or provide AI-generated footage.

This is a separately installed project. Core does not install or activate it. No new service,
core workflow node type, target lease or session lease is introduced. Target use is coordinated
by people. Customer-specific workflow configuration belongs in operator-owned storage.

## Source review first

**Do not install this on a production control plane, import its live skill, change grants,
credentials or workflow packs, restart services, or trigger a production demo until the source
has been reviewed and rollout authorized.** The examples are source-review material. The
connector starts disabled; the example pack is not in the default workflow-pack directory.

See [operations](docs/operations.md) for the later, separately gated rollout and recovery steps,
and [interfaces](docs/interfaces.md) for the worker, connector and capture contracts.

## Package and local verification

```bash
uv pip install -e 'packages/demo-video-addon[dev]'
npm ci --ignore-scripts --prefix packages/demo-video-addon
PYTHONPATH=packages/demo-video-addon:src timeout 360s .venv/bin/pytest -q packages/demo-video-addon/tests
evander-demo-video example-workflow
```

The portable worker has no Python package dependencies. It uses an offline FFmpeg/ffprobe/font
bundle and the session's existing seeded Playwright runtime. Recording and encoding execute
inside the isolated session. Narration uses `call_connector`; provider settings, authentication,
operation grants and approvals remain on the host. MP4 and transcript publication uses the
existing communication-artifact and Azure DevOps PR attachment tools. Teams delivery uses
`teams_post_file` with existing session permissions and an idempotency key.

Source fixtures use synthetic identities, a local application and a local speech substitute.
They prove browser capture, assembly and timing; they do not establish provider account access,
production deployment success or the quality of the configured provider voice.

Narration defaults to `gpt-realtime-2.1-mini` through a host-owned Realtime connection with Cedar. Custom Speech API providers remain configurable. Existing settings use the explicit [one-time migration](../../docs/openai-deprecation-migration.md); they are not rewritten by runtime loaders.
