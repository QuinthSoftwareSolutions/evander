# Voice in shared browser sessions

Voice is optional and disabled by default. It uses the same task, transcript and browser permissions as typed chat.

## Operator setup

Install the updated core and Browser Sessions 0.2 package, update the Chrome extension, and restart Admin Web and the browser relay through normal deployment before enabling voice.

In **Shared Browsers → Configure → Voice**, enable voice for the directory and select the speech API base URL, transcription model, speech model and voice. The defaults use the OpenAI Audio API with gpt-transcribe, gpt-4o-mini-tts and coral. The API contract is documented in [file transcription](https://developers.openai.com/api/docs/guides/speech-to-text) and [text to speech](https://developers.openai.com/api/docs/guides/text-to-speech).

Provision the environment variable named by api_key_env (default EVANDER_BROWSER_VOICE_API_KEY) in both Admin Web and the browser relay service environments. Speech uses that provider credential independently of the agent's model authentication. The worker, extension and page receive no speech API credential. No additional worker ports or service are needed.

Use HTTPS for the public control-plane URL and remote speech providers. Chrome microphone capture requires a secure context and explicit browser permission. Review speech-provider data handling before enabling it for the intended directory.

## Hosted viewers

Enable the microphone once. Hold **Talk** with the mouse or Space/Enter, speak, then release. Releasing sends a bounded clip for transcription and posts the recognized words to the existing viewer chat. The page annotation selected before recording accompanies that message.

Enable **Speak new replies** to hear subsequent agent replies. Text remains visible in the chat. **Stop audio**, starting another recording or ending the session interrupts local playback. Losing focus or hiding the page cancels microphone capture. Browser input still follows the session's existing control grant.

## User-hosted tabs

After pairing the extension with the application tab, choose **Open voice chat**. This opens a companion page on the control-plane origin. Keep it beside the application tab or in another window.

The companion uses a separate expiring capability tied to that browser and host. It can read/send that session's chat and use speech. It cannot grant browser input, approve an origin, close sharing or access other sessions. The capability travels in a URL fragment, is removed from browser history after admission, and remains in page memory. Reloading requires opening voice chat again from the extension. Admin sign-in is not required for this invited local host.

## Limits and recovery

Defaults are 45 seconds per recording, 2 MB per uploaded clip, 20 speech requests per minute per browser session, two concurrent requests per session and eight across the relay/Admin store. Advanced settings can adjust recording bytes, duration, rate and the spoken prefix length. Speech generation reads up to 2,000 reply characters by default; full replies remain in text. Audio uploads have a 20-second deadline. Provider requests have a 40-second overall deadline and output is bounded to 8 MB of WAV audio.

Raw microphone clips and generated reply audio are held in memory during processing and are not saved by Evander. Provider retention is governed by the configured provider. Transcription text, request fingerprints, outcome records and small audit entries follow the session's normal retention policy. Idempotent transcription retries return the completed text; uncertain provider requests are never automatically replayed. Stopping local playback aborts the download, though a provider may finish a request already accepted.

If microphone access is denied, repair the site's Chrome microphone permission and try enabling it again. If voice is unavailable, check directory settings and the credential in both host services. A closed, expired or revoked session stops voice access. Text chat remains available when voice is disabled or a speech request fails.

## Validation

The automated suite uses a real Chrome microphone recorder with a synthetic input device, real WAV playback, actual extension pairing, scoped companion access and a loopback speech provider. Unit/HTTP checks cover login, permissions, origin validation, bounded uploads, request deduplication, provider failure, rate limits and cancellation. No billable speech API call is made by these tests. Real microphone devices, provider credentials, selected languages/voices and enterprise browser policy still need deployment qualification.

Hands-free turn detection and precise narration timing alongside agent actions remain future extensions to this bounded push-to-talk flow.
