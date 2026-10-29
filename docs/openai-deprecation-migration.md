# OpenAI audio model migration

New browser speech and demo narration configurations use the host-owned Realtime adapter with `gpt-realtime-2.1-mini` and Cedar. Live call transcription defaults to `gpt-live-transcribe`; bounded recording transcription uses `gpt-transcribe`. The main conversational default remains `gpt-realtime-2`. Models and provider endpoints remain configurable.

Existing settings are changed only by `scripts/migrate_openai_deprecations.py`. Runtime loaders never translate deprecated IDs or silently alter voices/speeds. The script contains the explicit replacement tables from [OpenAI's deprecation schedule](https://developers.openai.com/api/docs/deprecations).

## Preview and apply

Production source changes require human review and the established CI/CD pipeline. During that pipeline's maintenance/migration step, install the reviewed core and already-used add-on packages, quiesce configuration writers and voice admission, run the migration, then reopen traffic with the reviewed release. Do not substitute manual package installation or service restarts for the pipeline. The migration is never invoked by service startup.

Generate a preview with the reviewed release's Python environment. Create the plan directory outside Git checkouts first, using operator-owned storage:

```bash
umask 077
.venv/bin/python scripts/migrate_openai_deprecations.py \
  --runtime-dir /var/lib/evander \
  --env-file /etc/evander/didww-sip-bridge.env \
  > /var/backups/evander/openai-migration-plan.json
```

Use the actual configured runtime and explicit bridge environment paths. `--env-file` can be repeated, or omitted when no bridge environment file needs migration; environment-only previews may omit `--runtime-dir`. The preview reads existing state without initializing stores, changing revisions or making provider requests. It includes model/backend/voice/speed changes, source revisions and hashes, never credential values. Configured browser/narration consumers require their reviewed add-on packages to validate candidate settings.

Review the preview, then apply exactly those targets:

```bash
.venv/bin/python scripts/migrate_openai_deprecations.py \
  --apply --plan /var/backups/evander/openai-migration-plan.json \
  --backup-dir /var/backups/evander/model-migrations --maintenance
```

Apply requires an explicit maintenance acknowledgement and recovery storage outside Git checkouts. It recomputes the preview and rejects stale or edited plans before writing. Directory changes use the platform configuration lock/store; browser settings use their transactional revision check. Bridge files are hash-checked and replaced atomically while preserving permissions. Recovery copies are written with mode `0600` before any settings change; protect their parent directory because these copies can contain credentials.

## Changes and preservation

- Known deprecated OpenAI speech models move to `gpt-realtime-2.1-mini` with `speech_backend: realtime`. Unsupported built-in voices (`onyx`, `nova`, `fable`) become `cedar`; supported voice selections remain. Speeds above `1.5` are clamped to `1.5` only by this migration.
- Known deprecated live transcription selections become `gpt-live-transcribe`; bounded browser recording transcription uses `gpt-transcribe`. Legacy conversational Realtime and mini selections become `gpt-realtime-2.1` and `gpt-realtime-2.1-mini` respectively.
- Custom endpoints retain their model, voice and speed selections. Existing Speech API configurations receive an explicit `speech_api` backend. Where a custom child endpoint inherits a model from a public OpenAI parent, the script pins its previous effective selection before the parent changes.
- Only owning configuration records are changed. Ordinary inherited settings do not gain child overrides. The script does not modify credentials, workflow packs, session history or pricing records. Explicitly disabled transcription remains disabled.

Realtime speech supports speeds from `0.25` to `1.5`; the compatible Speech API path retains `0.25` to `4`. New invalid values are rejected rather than clamped. Requests stay on the host and retain existing authorization, byte/time limits, streamed PCM playback and session-owned WAV artifacts.

## Verification and recovery

Rerun preview after applying: there should be no pending changes for the selected scope. Applying an already completed plan is a no-op. Check effective directory/browser settings, then verify representative speech, numbers, pacing, transcription and interruption in the intended test environment before production traffic resumes. Offline tests do not prove provider account access or real-call quality.

If a revision conflict or write failure stops a partially applied migration, its result identifies completed targets and the private recovery directory. Keep traffic quiesced and generate a fresh preview for the remaining targets. There is no automatic rollback across independent stores. Use supported configuration APIs and the reviewed CI/CD rollback process if recovery is necessary; do not restore live SQLite files directly.
