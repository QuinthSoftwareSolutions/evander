---
name: demo-video
description: Produce a narrated explanation of a pinned PR after its demo build has been deployed, using browser recordings and explanatory cards. Also perform the requested media readiness check before deployment.
---

# Demo video

Explain the change to a colleague who knows the business: what changed, why it matters,
and how to use it. Speak like a person. Avoid code names, internal plumbing and jargon
unless needed to understand the feature. Explain unfamiliar terms as they arise.

Browser footage supports the explanation. It does not limit its scope. Cover material
backend behavior with a practical example, even when it has no visible UI. For a change
combining calculations and form controls, explain the calculation's purpose and inputs,
work through a small checked example, and show how the controls affect the result.
A tour of the form alone is insufficient. Backend-only changes can use relevant browser
views and simple cards. Distinguish observed results, explanations grounded in source/tests,
and illustrative examples. Show before/after only when both sides have evidence.

Aim for two to three minutes; the maximum is five. Use English narration and readable
optional captions. Keep captions out of the video picture. A separate `captions.srt` file
lets viewers enable English captions in a compatible player. The final deliverables are an MP4 and plain-text transcript in the original PR
thread. Do not claim that deployment or a completed agent run proves the demonstration.

## Tools and boundaries

The reviewed toolchain is `/home/agent/.evander/demo-video/current`. Run its standalone
`worker.py` with Python 3; import its `capture.mjs` from Node. Use the existing installed
Playwright runtime (`EVANDER_PLAYWRIGHT_RUNTIME`) and seeded launch helper. Set
`PLAYWRIGHT_BROWSERS_PATH` to `current/playwright-cache` **before importing Playwright**;
the seeded Chromium executable path remains the one already granted to this session.
The runtime must support `page.screencast` recording with a first-frame callback; readiness
checks this capability before deployment.
No guest package downloads, browser downloads or direct speech-provider requests are needed.

Use the supplied managed website target alias. Never enter, print or save credentials,
cookies or browser state. People coordinate demo target usage manually. Preserve unrelated
data and existing local work. Prepare bounded synthetic data as authorized by the request;
do not redeploy, reset databases, modify application source or push commits.

## Readiness requests

A readiness-only request happens before deployment. Do not navigate to or modify the app.

1. Call the granted `demo-narration.synthesize` operation through `call_connector` with
   `{"text":"Narration is ready."}`. Respect approvals. Use
   `communication_copy_artifact_to_workspace` to obtain the returned WAV.
2. Import `preflightCapture` from `current/capture.mjs`, pass the installed `chromium`,
   and set `DEMO_VIDEO_PREFLIGHT_DIR` to a disposable job directory. It records an empty
   local page and returns `recording`, proving the actual video encoder is usable.
3. Run `python3 current/worker.py preflight --toolchain current --audio <copied WAV>
   --recording <returned WebM> --receipt <supplied /artifacts path>
   --request-key <supplied key>`. Use the absolute `current` path in actual commands.

The deterministic helper writes the readiness receipt only after all checks pass. If a
grant, approval, provider setting or tool is unavailable, stop and explain the issue;
do not hand-write the receipt. A readiness request is not an instruction to record the demo.

## Prepare and record

Inspect the pinned PR description, diff, relevant implementation and available tests.
Check the actual build revision too: a merge build can differ from the PR source commit.
Refresh a read-only checkout if necessary, preserving local changes. Rehearse the useful
scenario before recording. Verify worked-example arithmetic independently.

Keep all media in a dedicated request directory outside application source. Name it from
the immutable PR feedback identity; reuse that directory for recovery. Create `manifest.json`
with this contract:

```json
{
  "schema_version": 1,
  "request": {
    "repo": "sample-app", "pr_id": "42", "feedback_id": "feedback-1",
    "title": "A practical explanation", "target": "demo1",
    "summary": "Explain the calculation and show how the controls change the result.",
    "source_commit": "1111111111111111111111111111111111111111",
    "build_commit": "2222222222222222222222222222222222222222",
    "build_run_id": "123", "build_number": "2026.10.27.1"
  },
  "limitations": "State any unverified behavior or demonstration gaps here.",
  "scenes": [{
    "id": "why", "kind": "card", "title": "Why this matters",
    "body": "A short, concrete explanation of the user's problem.",
    "narration": "A short sentence that explains the practical benefit.",
    "evidence": [{"kind": "source", "reference": "Pinned PR and relevant implementation"}]
  }]
}
```

Replace every example identity and commit with the supplied pinned values. Scenes are short
narrated beats. Use evidence kinds `observed`, `source` or `illustration`, with specific
references. Keep each narration to at most three caption lines (roughly 30 words); split a
long explanation into beats. Cards accept a short title and at most seven short body lines.

For browser scenes, call `createRecorder({chromium, jobDirectory})`, then
`recordScene({id, targetAlias, setup, act, verify})`. `setup(page)` navigates to the initial
view before recording begins, `act(page, mark)` performs the deliberate action, and `verify(page, mark)` asserts the
visible result. Use `mark("meaningful-result")` for useful moments. Close the recorder in
`finally`. It returns a relative `clip`, `trim_start`, `trim_end`, markers and a digest.
Put those trim values, `clip`, `markers` and `result_seconds` into a `kind: "browser"`
scene with narration and evidence.
An optional `hold_before` adds presentation time on the initial frame.
Frame important controls and results fully in view.
Scroll to reveal the actual result before verification; text in an off-screen element is
insufficient evidence that the video shows it.

The helper reuses completed recordings. An interrupted/failed action has an uncertain
outcome and is not replayed automatically: inspect the app before deciding on a fresh scene.
This prevents recovery from silently repeating mutations.

## Narrate and assemble

After recording, write narration that matches the actual footage and evidence. For each
beat, call `demo-narration.synthesize` with only `text`, copy its WAV artifact into the
workspace, and run `worker.py import-audio --manifest <manifest> --scene <id>
--source <copied WAV>`. This binds the audio receipt to that exact text. Reuse successful
audio for retries; changed text requires new audio.

Run `worker.py render --manifest <manifest> --toolchain current`. The helper pairs each
short narration with its scene, trims setup/idle footage, pads useful frames when speech
is longer, and keeps browser actions at normal speed. It produces `explanation.mp4`,
`transcript.txt` and a measured timeline in the manifest. Output is 720p, 24 fps, H.264/AAC,
with no caption overlays, at most five minutes and 50 MiB. It also saves optional `captions.srt`
in the workspace. It includes an AI-narration disclosure.

Inspect representative frames and listen to the narration against the footage. Check
that the feature's purpose and example are explained, controls and results are readable,
and the optional captions follow the narration when enabled,
and assertions are supported. Adjust and regenerate only affected beats. Run
`worker.py verify --manifest <manifest> --toolchain current` before publication.

## Publish and recover

Use `communication_stage_workspace_file` for the MP4 and transcript. Preserve file approval
rules. After each successful staging, run `worker.py stage-receipt --manifest <manifest>
--kind video|transcript --artifact-id <returned ID>`.
The optional SRT may also be staged as plain text and recorded with `--kind captions` when
captions are requested. Retain its successful receipt on retries just like the other files.

Run `worker.py publication --manifest <manifest>`. If `published` is true, return its existing
reply; otherwise use its `body_text`, `artifact_ids` and `idempotency_key` with
`post_pr_feedback_reply` on the supplied provider/repo/PR/thread/comment. Include meaningful
limitations and the supplied public demo/build links in the reply. Only after a successful
posted reply, run `worker.py published --manifest <manifest> --reply-id <returned reply ID>`.
If the tool outcome is uncertain, retry the same idempotency key; never post with a fresh key.

Completed clips, audio, encoded segments and staging receipts are checkpoints. Resume media
or publication failures from the appropriate checkpoint, without redeploying or redoing known
completed actions. If files are lost or application state is uncertain, stop and report it.
Keep published requests immutable; a revised explanation needs a new PR comment.
