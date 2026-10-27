# Operation and source-review handoff

## Review gate

The initial delivery is source, tests, synthetic sample videos and these instructions.
Production packages, services, skills, credentials, grants, directory configuration and workflow
packs remain untouched until the source has been reviewed and rollout authorized.

Keep operator-specific hostnames, pipeline IDs, account identities and adapted packs outside
the repository. The checked-in example deliberately uses nonexistent synthetic infrastructure.

## Local source qualification

From the repository worktree, install the add-on only in its development environment:

```bash
uv sync --frozen --extra dev
uv pip install -e 'packages/demo-video-addon[dev]'
npm ci --ignore-scripts --prefix packages/demo-video-addon
timeout 360s .venv/bin/pytest -q packages/demo-video-addon/tests
```

Use explicit command-level timeouts for browser/media suites. Terminate a timed-out command,
retain its last useful output and continue with focused checks. The add-on does not make
external provider calls in its automated suite.

For an end-to-end local review, provide Node, an installed Playwright module/browser cache,
FFmpeg with the `flite` speech filter, ffprobe and DejaVu Sans:

```bash
timeout 600s .venv/bin/python packages/demo-video-addon/scripts/build_review_samples.py \
  --output /tmp/demo-video-review \
  --node /path/to/node \
  --playwright-module /path/to/playwright/index.js
```

This starts an ephemeral loopback application, a synthetic managed-login broker and a local
Speech API substitute. It exercises the real seeded launch helper, actual recordings,
the real narration connector and media assembly. It makes no production or external
speech-provider requests. Flite voice quality is **not** evidence of the configured provider
voice. The three examples cover a calculation plus UI controls, a save-state bugfix and a
backend explanation with contextual browser views/cards. The local report endpoint computes
the backend figure; `backend/report-response.json` records its independently checked result.
The bugfix fixture checks that editing a stored note leaves the saved version intact while
showing unsaved changes. Review their MP4s, transcripts,
frames and `review.json`; distinguish this proof from later provider/production acceptance.

## Presentation guidance updates

Keep presentation rules in the packaged `demo-video/SKILL.md`; the example workflow passes
request/build/target context and refers to that skill. Review guidance changes in source before
updating the managed copy. Keep application-specific preparation instructions outside this
customer-neutral package.

After source review and authorized CI/CD delivery, import the reviewed skill through the
managed Skills surface. Let the workflow's skill-installation step refresh the session copy;
an already imported skill does not change merely because its package source changed. Adapt
an existing workflow brief through the normal fresh-read, validation and revision-gated save
process so it does not duplicate or contradict the managed presentation rules.

The generic narration-delivery wording is versioned in `examples/connectors.json`. Apply a
reviewed wording change to the intended connector's `config.instructions` through supported
configuration controls. Preserve the existing endpoint, credential reference, model, voice,
speed, operation grants and approval rules; do not replace live configuration with the disabled
example. The agent continues to supply narration text only.

For the next separately authorized sample, check that material UI changes are actually used,
related actions and visible outcomes are shown, backend meaning remains clear, and cards add
necessary explanation without dominating an available UI demonstration. Watch and listen for
conversational delivery and readable pacing. Automated media checks do not establish those
qualities. Reuse the existing recording/rendering toolchain for these guidance changes.

## Prepare offline tools

After source review, prepare a release artifact from trusted Linux tools compatible with the
worker architecture, in the build environment:

```bash
.venv/bin/evander-demo-video prepare-toolchain \
  --output /tmp/demo-video-release \
  --playwright-cache /path/to/host-seeded/ms-playwright
```

The builder copies FFmpeg, ffprobe, their ELF loader/runtime libraries, the font, worker helper,
capture helper and host-seeded `ffmpeg-*` Playwright recording revisions. It verifies the
relocated tools before creating an archive and SHA-256 inventory. Retain the bundled tools'
licenses and use an approved redistribution source. This is optional tooling; base VMs and
default runner images do not gain a mandatory media dependency.
Publish this reviewed bundle through the established CI/CD pipeline. Do not copy source or
built assets into the running control-plane installation or manually restart it to activate them.

`effect:agent:copy-files` copies the prepared archive and portable `worker.py` into the existing
session. The example executes its offline installer before deployment. The installer rejects
duplicate/traversing archive paths, links, devices, extraction-budget excess and digest mismatch,
then selects a validated private toolchain. No guest internet package installation is used.
Chromium and Node must already be provisioned through the normal Playwright bootstrap.
Set `PLAYWRIGHT_BROWSERS_PATH` to the bundle's private `playwright-cache` before importing
Playwright; Chromium still uses the existing seeded executable path. Include the recording
encoder revision required by that installed Playwright version.

## Later production activation

Perform this only after Jonathan's source review and the later rollout authorization:

1. Deploy the separately packaged, human-reviewed add-on through the established CI/CD pipeline.
   Add its module and add-on ID to the existing loader configuration without dropping other entries;
   use supported configuration/readback surfaces and pipeline activation. Do not manually reinstall
   packages, switch the running checkout, copy source/assets into it, or restart services to activate code.
   Core installation alone does not install or enable this project.
   The example's Azure DevOps nodes also require the separately installed/enabled Azure DevOps add-on.
2. Use the pipeline-published, validated offline media bundle. Import the packaged `demo-video/SKILL.md`
   through the supported managed Skills surface. Add any target-specific preparation skills
   to the operator-owned workflow; the generic skill does not contain application secrets.
3. Adapt `examples/connectors.json` using the existing host speech endpoint, model, voice and
   credential reference. Prefer the supported managed credential controls for live secrets.
   The example is disabled and approval-required. Preserve approvals and configure any
   automation allowance explicitly in the intended directory, not globally.
4. Add only the required tools/operation to intended review sessions and future provisioning:
   `call_connector` with `demo-narration.synthesize`,
   `communication_copy_artifact_to_workspace`, `communication_stage_workspace_file`,
   and `post_pr_feedback_reply`. Enable necessary file reception/sending; allow WAV, MP4 and
   text, with a 50 MiB ceiling for the intended sessions. Merge existing MIME/extension/tool
   allowances rather than overwriting unrelated grants. Preserve file approval requirements.
   If SRT publication is wanted, allow `.srt` plain-text caption files in the same scope.
5. Read a fresh live workflow editor document and adapt the generic example's directory, PR
   criteria, bot identities, session selector, targets, host tool paths and build/deployment
   pipeline contract. Set an effective graph-depth budget of at least 64. Add `!EXPLAIN`
   before the ordinary agent-directed feedback filter; connect the example's `standard`
   route to the existing feedback path so `!TEST` and other commands retain their behavior.
   Keep connected edges in the same pack. Never replace a live pack from a stale source copy.
6. Resolve and test the exact-origin managed login for the selected target. Browser origin,
   login origin and configured profile must match that environment. The workflow replies
   explicitly if no matching ready profile exists.
7. Validate the candidate, save through the revision-gated API with the freshly read expected
   revision/lock, reread normalized config/layout/history and confirm orchestrator adoption.
   Retain the prior revision for a similarly gated rollback. Never write live pack files directly.
8. Coordinate a target in person and run one approved canary command. Verify the actual build
   revision, deployment outcome, browser behavior, explanation, narration/caption timing, audio
   quality, clear video picture, optional SRT/transcript and PR attachment access. Then verify a duplicate delivery and
   a recoverable media/publication failure without a second deployment. Only then enable normal use.

There is no target/session lease. The existing pre-deployment busy check is retained; people
coordinate subsequent use. An operator changing the target during a recording can invalidate
the demonstration and requires inspection before continuing.

## Workflow behavior and recovery

The canonical `examples/pr-explain.v1.json` is generated by
`evander-demo-video example-workflow`. It uses existing trigger/gate/query/effect types.
The generic source example is not automatically loaded by the control plane.

The workflow requires a unique existing review session and a successful build matching the
PR's source or merge revision. It never queues a new build. It waits up to 60 minutes for a
matching running build and rechecks source/merge revisions and active status before deployment.
The deployment contract preserves the database, performs minimal configuration and skips seeding.

Before deployment, the workflow renews the existing session, copies/validates media tools and
checks the existing conversation and queues a readiness-only turn in that same context.
A missing conversation stops preparation with a PR reply. The readiness turn tests an empty-page recording and one short
synthetic speech request, follows existing approvals, and runs the deterministic readiness
helper. The workflow reads its request/build-specific receipt before proceeding. A failed or
missing receipt prevents deployment. After deployment it grants only the configured targets
and queues the demonstration in the existing conversation. Application-specific readiness and
grants are operator prerequisites, not permission for the agent to acquire capabilities.

An agent-completed marker only records workflow execution completion. The posted video and
its evidence establish the delivered result. On failures inspect the phase, prompt queue,
request manifest, scene receipts and existing PR replies before retrying. A queued turn is
retried through the existing native queue; do not post another command while it is pending.

The capture helper refuses to repeat an uncertain browser action. Successful audio/rendering
checkpoints and artifact receipts are reusable. A publication retry uses the same idempotency
key and files. A new command creates a new request and can redeploy; use that deliberately.
If the session workspace was lost, media checkpoints cannot be assumed recoverable.

Retain/delete the request media according to the existing session/artifact privacy policy.
PR attachments have the PR provider's lifetime after upload. No new public video hosting or
access tokens are introduced. Report provider size/preview limitations honestly; v1 promises
an MP4 attachment link, not inline playback in every Azure DevOps client.

## Speech API retirement

V1 intentionally retains the current Speech API. OpenAI schedules `tts-1`, `tts-1-hd` and
`gpt-4o-mini-tts` variants for removal on **January 6, 2027**. Plan and review one coordinated
migration for existing voice users and this connector before that date; do not migrate browser
voice users incidentally while deploying demo videos. The authoritative notice is
[OpenAI's deprecation page](https://developers.openai.com/api/docs/deprecations#2026-10-01-text-to-speech-models).
