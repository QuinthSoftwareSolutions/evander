# Voice in shared browser sessions

Voice is optional and disabled by default. It uses the same task, transcript and browser permissions as typed chat.

## Operator setup

Install the updated core and Browser Sessions 0.2 package, update the Chrome extension, and restart Admin Web and the browser relay through normal deployment before enabling voice. Install FFmpeg with the ffmpeg executable available on the PATH of both Admin Web and the browser relay; only optional microphone transcription needs it. If it is missing, text and spoken replies remain available.

In **Shared Browsers → Configure → Voice**, enable voice for the directory and select the speech API base URL, transcription model, speech model and voice. The defaults use the OpenAI Audio API with gpt-transcribe, gpt-4o-mini-tts and coral. The API contract is documented in [file transcription](https://developers.openai.com/api/docs/guides/speech-to-text) and [text to speech](https://developers.openai.com/api/docs/guides/text-to-speech).

Provision the environment variable named by api_key_env (default EVANDER_BROWSER_VOICE_API_KEY) in both Admin Web and the browser relay service environments. Speech uses that provider credential independently of the agent's model authentication. The worker, extension and page receive no speech API credential. No additional worker ports or service are needed.

Use HTTPS for the public control-plane URL and remote speech providers. Chrome microphone capture requires a secure context and explicit browser permission. Review speech-provider data handling before enabling it for the intended directory.

## Hosted viewers

Enable the microphone once. Hold **Talk** with the mouse or Space/Enter, speak, then release. Releasing sends a bounded clip for transcription and posts the recognized words to the existing viewer chat. The page annotation selected before recording accompanies that message.

Enable **Speak new replies** to hear subsequent agent replies. Text remains visible in the chat. **Stop audio**, starting another recording or ending the session interrupts local playback. Moving keyboard focus away from Talk, losing window focus or hiding the page cancels microphone capture. Browser input still follows the session's existing control grant.

## User-hosted tabs

With Chrome extension **0.3.0** or later, pair the application tab and expand **Voice** in the extension sidebar. Choose **Enable microphone**, then hold **Talk** and release to send. **Speak new replies** and **Stop audio** operate in the same sidebar. Typed and spoken messages share its existing transcript. No separate voice tab is opened.

Chrome can require a one-time extension-origin microphone grant. When access has not yet been granted, a small **Enable microphone** permission window opens. Choose **Allow microphone**, approve Chrome's prompt, and return to the sidebar; the permission window closes automatically. It stops its test stream without recording or uploading audio. Cancelling closes setup and permits another attempt. Once permission is granted, subsequent setup needs no extra window. This handles Chrome's [side-panel permission limitation](https://groups.google.com/a/chromium.org/g/chromium-extensions/c/cJmdMLmpbjg/m/zYGc54AaAQAJ).

The sidebar obtains a separate expiring voice capability tied to that browser and host. It can use only that session's chat and speech endpoints. It cannot grant browser input, approve an origin, close sharing or access other sessions. The capability stays in extension page memory and is never placed in a page URL or persisted by the voice UI. Only the extension sidebar can request it from the background worker. Extension-origin voice requests still require that capability; hosted voice mutations retain their control-plane origin checks.

Collapsing or hiding the voice UI, closing the sidebar, ending sharing or pairing a replacement session stops microphone tracks and playback, cancels permission setup and aborts pending transcription. Voice never switches its capability or an unfinished message to another session. Reopen the controls and enable the microphone to resume.

Older extensions can continue using the existing companion voice page. Its voice capability travels in the URL fragment and is removed from browser history after admission. The current extension keeps this flow entirely in its sidebar. Admin sign-in and browser input control are not required for the invited local host's voice chat.

## Limits and recovery

Defaults are 45 seconds per recording, 2 MB per uploaded clip, 20 speech requests per minute per browser session, two concurrent requests per session and eight across the relay/Admin store. Advanced settings can adjust recording bytes, duration, rate and the spoken prefix length. Speech generation reads up to 2,000 reply characters by default; full replies remain in text. Audio uploads have a 20-second deadline. The host decodes accepted containers to mono 16 kHz WAV, counts decoded samples and rejects recordings above the configured duration before contacting the provider. FFmpeg has pipe-only access, a 10-second deadline and a bounded output frame count; interrupted decoders are killed and reaped. Provider requests have a 40-second overall deadline and output is bounded to 8 MB of WAV audio.

Raw microphone clips, normalized transcription audio and generated reply audio are held in memory during processing and are not saved by Evander. Provider retention is governed by the configured provider. Transcription text, request fingerprints, outcome records and small audit entries follow the session's normal retention policy. Idempotent transcription retries return the completed text; uncertain provider requests are never automatically replayed. Stopping local playback aborts the download, though a provider may finish a request already accepted.

If microphone access is denied, repair the site's Chrome microphone permission and try enabling it again. If voice is unavailable, check directory settings and the credential in both host services. A closed, expired or revoked session stops voice access. Text chat remains available when voice is disabled or a speech request fails.

## Validation

The automated suite uses a real Chrome microphone recorder with a synthetic input device, real WAV playback, actual extension pairing, sidebar capture, microphone permission cancellation/retry, scoped legacy companion access and a loopback speech provider. Unit/HTTP checks cover login, permissions, origin validation, bounded uploads, request deduplication, provider failure, rate limits and cancellation. No billable speech API call is made by these tests. Real microphone devices, provider credentials, selected languages/voices and enterprise browser policy still need deployment qualification.

Hands-free turn detection and precise narration timing alongside agent actions remain future extensions to this bounded push-to-talk flow.
