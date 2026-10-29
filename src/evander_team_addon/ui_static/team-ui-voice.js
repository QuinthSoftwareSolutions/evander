// Voice session state, browser media lifecycle, and realtime events.
import {
  delay,
  escapeAttr,
  escapeHtml,
  responseText,
  text,
} from "./team-ui-format.js";
import { API_BASE } from "./team-ui-config.js";

export function createTeamVoiceController(peers) {

  let activeAgentVoiceCall = null;

  function renderAgentVoiceCallPanel(details) {
    const call = activeAgentVoiceCall?.agentId === details.id ? activeAgentVoiceCall : null;
    const liveCallForOtherAgent =
      activeAgentVoiceCall && !agentVoiceCallIsTerminal(activeAgentVoiceCall) && activeAgentVoiceCall.agentId !== details.id;
    const callDisabled = details.status === "archived" || liveCallForOtherAgent;
    const voiceProfileLabel = agentVoiceProfileLabel(details.voice_profile);
    const transcriptRows = (call?.transcript || [])
      .slice(-4)
      .map((entry) => {
        const speaker = text(entry.speaker) || "voice";
        return `<li><span>${escapeHtml(speaker)}</span>${escapeHtml(entry.text)}</li>`;
      })
      .join("");
    if (!call || agentVoiceCallIsTerminal(call)) {
      return `
        <section class="agent-detail-section agent-voice-call-panel">
          <div class="agent-voice-call-header">
            <h3>Voice</h3>
            <span class="state-pill neutral">idle</span>
          </div>
          <div class="record-actions agent-voice-call-controls">
            <button class="record-action" type="button" data-agent-call-agent-id="${escapeAttr(details.id)}" ${callDisabled ? "disabled" : ""}>Call</button>
          </div>
          <div class="meta-line">Voice: ${escapeHtml(voiceProfileLabel)}</div>
          ${call?.error ? `<div class="meta-line agent-voice-error">${escapeHtml(call.error)}</div>` : ""}
        </section>
      `;
    }
    const retryEnd = call.cleanupRequested || call.status === "end_failed";
    const endingDisabled = call.status === "ending" ? "disabled" : "";
    return `
      <section class="agent-detail-section agent-voice-call-panel">
        <div class="agent-voice-call-header">
          <h3>Voice</h3>
          <span class="state-pill ${escapeAttr(agentVoiceCallPillClass(call))}">${escapeHtml(agentVoiceCallStatusLabel(call))}</span>
        </div>
        <div class="record-actions agent-voice-call-controls">
          ${retryEnd ? "" : `<button class="record-action" type="button" data-agent-call-mute>${call.muted ? "Unmute" : "Mute"}</button>`}
          <button class="record-action" type="button" data-agent-call-end ${endingDisabled}>${retryEnd ? "Retry end" : "End"}</button>
        </div>
        <div class="meta-line">Voice: ${escapeHtml(voiceProfileLabel)}</div>
        ${call.error ? `<div class="meta-line agent-voice-error">${escapeHtml(call.error)}</div>` : ""}
        ${transcriptRows ? `<ol class="agent-voice-transcript">${transcriptRows}</ol>` : ""}
      </section>
    `;
  }

  function agentVoiceProfileLabel(profile) {
    const voice = text(profile?.voice) || "Default";
    const style = text(profile?.speaking_style);
    return style ? `${voice} - custom style` : voice;
  }

  function agentVoiceCallIsTerminal(call) {
    return !call || call.status === "ended" || call.status === "failed";
  }

  function agentVoiceCallStatusLabel(call) {
    const status = text(call?.status) || "idle";
    if (status === "active") {
      return "live";
    }
    if (status === "querying") {
      return "querying";
    }
    if (status === "ending") {
      return "ending";
    }
    if (status === "end_failed") {
      return "end failed";
    }
    return status;
  }

  function agentVoiceCallPillClass(call) {
    const status = text(call?.status);
    if (status === "active" || status === "querying") {
      return "accepted";
    }
    if (status === "failed") {
      return "rejected";
    }
    if (status === "end_failed") {
      return "rejected";
    }
    if (status === "connecting" || status === "ending") {
      return "blocked";
    }
    return "neutral";
  }

  function renderAgentDetailField(label, value) {
    return `
      <div class="agent-detail-field">
        <span class="agent-detail-label">${escapeHtml(label)}</span>
        <strong class="agent-detail-value">${escapeHtml(value)}</strong>
      </div>
    `;
  }

  function renderOpenAgentSessionButton(sessionId) {
    const normalizedSessionId = text(sessionId);
    if (!normalizedSessionId) {
      return "";
    }
    return `<button class="record-action" type="button" data-open-session-id="${escapeAttr(normalizedSessionId)}">Open session</button>`;
  }

  function renderOpenAgentChatButton(agentId) {
    const normalizedAgentId = text(agentId);
    if (!normalizedAgentId) {
      return "";
    }
    return `<button class="record-action" type="button" data-open-chat-agent-id="${escapeAttr(normalizedAgentId)}" ${peers.chat.chatState.read_only ? 'disabled title="This chat history is read-only."' : ""}>Chat</button>`;
  }

  async function startAgentVoiceCall(button, agentId) {
    if (!peers.access.guardTeamMutation("start voice calls")) {
      return;
    }
    const normalizedAgentId = text(agentId);
    const conversationId = text(button?.dataset?.agentCallConversationId);
    if (!normalizedAgentId || !peers.app.state.agents[normalizedAgentId]) {
      peers.app.setStatus("No agent selected.", "error");
      return;
    }
    if (activeAgentVoiceCall && !agentVoiceCallIsTerminal(activeAgentVoiceCall)) {
      peers.app.setStatus("A voice call is already active.", "warning");
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia || typeof RTCPeerConnection === "undefined") {
      peers.app.setStatus("Browser voice calls require microphone and WebRTC support.", "error");
      return;
    }
    const call = {
      agentId: normalizedAgentId,
      voiceSessionId: "",
      requestId: createAgentVoiceRequestId(),
      status: "connecting",
      startupPhase: "microphone",
      muted: false,
      cancelRequested: false,
      cleanupRequested: false,
      transcript: [],
      handledFunctionCalls: new Set(),
      mediaStream: null,
      peerConnection: null,
      dataChannel: null,
      remoteAudio: null,
      callsUrl: "",
      clientSecret: "",
      error: "",
    };
    activeAgentVoiceCall = call;
    button.disabled = true;
    peers.people.renderAgents();
    peers.chat.renderChat();
    peers.app.setStatus("Connecting voice call.", "loading");
    try {
      const mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      call.mediaStream = mediaStream;
      if (!agentVoiceCallIsCurrent(call)) {
        cleanupAgentVoiceCall(call);
        return;
      }
      if (call.cancelRequested) {
        cleanupAgentVoiceCall(call);
        clearAgentVoiceCallIfCurrent(call);
        peers.app.setStatus("Voice call cancelled.", "ok");
        return;
      }
      call.startupPhase = "session";
      const response = await peers.access.teamFetch(
        `${API_BASE}/agents/${encodeURIComponent(normalizedAgentId)}/voice-sessions`,
        {
          method: "POST",
          headers: {
            Accept: "application/json",
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            request_id: call.requestId,
            conversation_id: conversationId || null,
          }),
        },
      );
      if (!response.ok) {
        throw new Error(await responseText(response));
      }
      const payload = await response.json();
      peers.chat.syncTeamStateFromChatPayload(payload);
      if (!agentVoiceCallIsCurrent(call)) {
        call.voiceSessionId = text(payload.voice_session_id);
        call.requestId = text(payload.request_id) || call.requestId;
        await endAgentVoiceSessionOnServer(call, "ended", "Cancelled during startup.")
          .catch(() => {});
        cleanupAgentVoiceCall(call);
        return;
      }
      call.voiceSessionId = text(payload.voice_session_id);
      call.requestId = text(payload.request_id) || call.requestId;
      call.clientSecret = realtimeClientSecretValue(payload);
      call.callsUrl = text(payload.realtime?.calls_url) || "https://api.openai.com/v1/realtime/calls";
      if (conversationId) {
        peers.chat.selectVoiceConversation(conversationId);
        void peers.chat.loadConversations({ selectedConversationId: conversationId });
      }
      if (!call.voiceSessionId || !call.clientSecret) {
        throw new Error("Voice call response was missing a session or client secret.");
      }
      if (call.cancelRequested) {
        await endAgentVoiceCallAfterStartupCancel(call);
        return;
      }
      call.startupPhase = "webrtc";
      await connectBrowserRealtimeVoice(call);
      if (!agentVoiceCallIsCurrent(call)) {
        cleanupAgentVoiceCall(call);
        return;
      }
      if (call.cancelRequested) {
        await endAgentVoiceCallAfterStartupCancel(call);
        return;
      }
      call.status = "active";
      call.startupPhase = "active";
      call.error = "";
      peers.people.renderAgents();
      peers.chat.renderChat();
      await recordAgentVoiceEvent(call.voiceSessionId, {
        status: "active",
        event_type: "browser_call.active",
      });
      if (!agentVoiceCallIsCurrent(call) || call.cancelRequested) {
        return;
      }
      peers.app.setStatus(`Voice call with ${peers.agents.agentLabel(peers.app.state.agents[normalizedAgentId])} is live.`, "ok");
    } catch (error) {
      if (!agentVoiceCallIsCurrent(call)) {
        cleanupAgentVoiceCall(call);
        return;
      }
      const message = error instanceof Error ? error.message : "Voice call failed.";
      await failAgentVoiceCall(call, message);
    } finally {
      if (button.isConnected) {
        button.disabled = false;
      }
    }
  }

  async function connectBrowserRealtimeVoice(call) {
    const peerConnection = new RTCPeerConnection();
    const dataChannel = peerConnection.createDataChannel("oai-events");
    const remoteAudio = document.createElement("audio");
    remoteAudio.autoplay = true;
    remoteAudio.hidden = true;
    remoteAudio.setAttribute("playsinline", "true");
    document.body.appendChild(remoteAudio);
    call.peerConnection = peerConnection;
    call.dataChannel = dataChannel;
    call.remoteAudio = remoteAudio;

    peerConnection.addEventListener("track", (event) => {
      const [stream] = event.streams;
      if (stream) {
        remoteAudio.srcObject = stream;
        void remoteAudio.play().catch(() => {});
      }
    });
    peerConnection.addEventListener("connectionstatechange", () => {
      const status = peerConnection.connectionState;
      if (status === "failed" && !agentVoiceCallIsTerminal(call) && call.status !== "ending") {
        void failAgentVoiceCall(call, `WebRTC connection ${status}.`);
      }
    });
    dataChannel.addEventListener("message", (event) => {
      void handleRealtimeAgentVoiceEvent(call, event.data);
    });
    call.mediaStream.getTracks().forEach((track) => {
      peerConnection.addTrack(track, call.mediaStream);
    });
    const offer = await peerConnection.createOffer();
    await peerConnection.setLocalDescription(offer);
    const response = await peers.access.teamFetch(call.callsUrl, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${call.clientSecret}`,
        "Content-Type": "application/sdp",
      },
      body: offer.sdp,
    });
    if (!response.ok) {
      throw new Error(await responseText(response));
    }
    await peerConnection.setRemoteDescription({
      type: "answer",
      sdp: await response.text(),
    });
  }

  function realtimeClientSecretValue(payload) {
    return (
      text(payload?.client_secret?.value) ||
      text(payload?.client_secret?.client_secret?.value) ||
      text(payload?.client_secret)
    );
  }

  async function recordAgentVoiceEvent(voiceSessionId, payload) {
    const normalizedVoiceSessionId = text(voiceSessionId);
    if (!normalizedVoiceSessionId) {
      return null;
    }
    const response = await peers.access.teamFetch(
      `${API_BASE}/voice-sessions/${encodeURIComponent(normalizedVoiceSessionId)}/events`,
      {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload || {}),
      },
    );
    if (!response.ok) {
      throw new Error(await responseText(response));
    }
    return response.json();
  }

  async function endAgentVoiceCall(button) {
    if (!peers.access.guardTeamMutation("end voice calls")) {
      return;
    }
    const call = activeAgentVoiceCall;
    if (!call) {
      return;
    }
    const wasConnecting = call.status === "connecting";
    button.disabled = true;
    call.cancelRequested = true;
    call.status = "ending";
    peers.people.renderAgents();
    peers.chat.renderChat();
    if (!call.voiceSessionId && !call.requestId) {
      cleanupAgentVoiceCall(call);
      clearAgentVoiceCallIfCurrent(call);
      peers.app.setStatus("Voice call cancelled.", "ok");
      if (button.isConnected) {
        button.disabled = false;
      }
      return;
    }
    if (!call.voiceSessionId && wasConnecting && call.startupPhase === "microphone") {
      cleanupAgentVoiceCall(call);
      clearAgentVoiceCallIfCurrent(call);
      peers.app.setStatus("Voice call cancelled.", "ok");
      if (button.isConnected) {
        button.disabled = false;
      }
      return;
    }
    if (!call.voiceSessionId && wasConnecting) {
      cleanupAgentVoiceCall(call);
      peers.app.setStatus("Cancelling voice call startup.", "loading");
      if (button.isConnected) {
        button.disabled = false;
      }
      return;
    }
    try {
      await endAgentVoiceSessionOnServer(call, "ended", "");
      cleanupAgentVoiceCall(call);
      call.status = "ended";
      clearAgentVoiceCallIfCurrent(call);
      peers.app.setStatus("Voice call ended.", "ok");
    } catch (error) {
      markAgentVoiceEndFailed(call, "Voice call end failed", error);
      if (button.isConnected) {
        button.disabled = false;
      }
      return;
    }
    if (button.isConnected) {
      button.disabled = false;
    }
  }

  function agentVoiceCallIsCurrent(call) {
    return activeAgentVoiceCall === call;
  }

  function createAgentVoiceRequestId() {
    if (globalThis.crypto?.randomUUID) {
      return `browser-call:${globalThis.crypto.randomUUID()}`;
    }
    return `browser-call:${Date.now()}:${Math.random().toString(16).slice(2)}`;
  }

  function clearAgentVoiceCallIfCurrent(call) {
    if (!agentVoiceCallIsCurrent(call)) {
      return;
    }
    activeAgentVoiceCall = null;
    peers.people.renderAgents();
    peers.chat.renderChat();
  }

  async function endAgentVoiceCallAfterStartupCancel(call) {
    try {
      await endAgentVoiceSessionOnServer(call, "ended", "Cancelled during startup.");
      cleanupAgentVoiceCall(call);
      call.status = "ended";
      clearAgentVoiceCallIfCurrent(call);
      peers.app.setStatus("Voice call cancelled.", "ok");
    } catch (error) {
      cleanupAgentVoiceCall(call);
      markAgentVoiceEndFailed(call, "Voice call was stopped locally, but end update failed", error);
    }
  }

  async function failAgentVoiceCall(call, message) {
    call.status = "failed";
    call.error = message;
    if (agentVoiceCallIsCurrent(call)) {
      peers.people.renderAgents();
      peers.chat.renderChat();
    }
    if (!call.voiceSessionId && !call.requestId) {
      cleanupAgentVoiceCall(call);
      clearAgentVoiceCallIfCurrent(call);
      peers.app.setStatus(message, "error");
      return;
    }
    try {
      await endAgentVoiceSessionOnServer(call, "failed", message);
      cleanupAgentVoiceCall(call);
      clearAgentVoiceCallIfCurrent(call);
      peers.app.setStatus(message, "error");
    } catch (error) {
      cleanupAgentVoiceCall(call);
      markAgentVoiceEndFailed(call, `${message} End update failed`, error);
    }
  }

  function markAgentVoiceEndFailed(call, prefix, error) {
    call.status = "end_failed";
    call.cleanupRequested = true;
    call.error = `${prefix}: ${error instanceof Error ? error.message : "retry required"}`;
    if (agentVoiceCallIsCurrent(call)) {
      peers.people.renderAgents();
      peers.chat.renderChat();
    }
    peers.app.setStatus(call.error, "error");
  }

  async function endAgentVoiceSessionOnServer(call, status, errorMessage) {
    let endpoint = "";
    if (call.voiceSessionId) {
      endpoint = `${API_BASE}/voice-sessions/${encodeURIComponent(call.voiceSessionId)}/end`;
    } else if (call.requestId) {
      endpoint = `${API_BASE}/voice-session-requests/${encodeURIComponent(call.requestId)}/end`;
    } else {
      return null;
    }
    const transcript = agentVoiceTranscriptExcerpt(call, { limit: 60 });
    const response = await peers.access.teamFetch(endpoint, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        status,
        transcript: transcript || null,
        error: errorMessage || null,
      }),
    });
    if (!response.ok) {
      throw new Error(await responseText(response));
    }
    const payload = await response.json();
    call.voiceSessionId = call.voiceSessionId || text(payload.voice_session_id);
    return payload;
  }

  function cleanupAgentVoiceCall(call) {
    try {
      call.dataChannel?.close();
    } catch {
      // Ignore close races from the browser WebRTC stack.
    }
    try {
      call.peerConnection?.close();
    } catch {
      // Ignore close races from the browser WebRTC stack.
    }
    call.mediaStream?.getTracks?.().forEach((track) => {
      track.stop();
    });
    if (call.remoteAudio) {
      call.remoteAudio.pause();
      call.remoteAudio.srcObject = null;
      call.remoteAudio.remove();
    }
    call.dataChannel = null;
    call.peerConnection = null;
    call.mediaStream = null;
    call.remoteAudio = null;
  }

  function toggleAgentVoiceMute() {
    if (!peers.access.guardTeamMutation("change voice calls")) {
      return;
    }
    const call = activeAgentVoiceCall;
    if (!call?.mediaStream) {
      return;
    }
    call.muted = !call.muted;
    call.mediaStream.getAudioTracks().forEach((track) => {
      track.enabled = !call.muted;
    });
    peers.people.renderAgents();
    peers.chat.renderChat();
  }

  async function handleRealtimeAgentVoiceEvent(call, rawEvent) {
    let event;
    try {
      event = JSON.parse(rawEvent);
    } catch {
      return;
    }
    if (event.type === "error") {
      const message = text(event.error?.message) || "Realtime voice error.";
      call.error = message;
      peers.people.renderAgents();
      peers.chat.renderChat();
      peers.app.setStatus(message, "error");
      return;
    }
    const transcript = realtimeTranscriptFromEvent(event);
    if (transcript) {
      call.transcript.push(transcript);
      call.transcript = call.transcript.slice(-40);
      peers.people.renderAgents();
      peers.chat.renderChat();
      if (call.voiceSessionId) {
        void recordAgentVoiceEvent(call.voiceSessionId, {
          text: transcript.text,
          speaker: transcript.speaker,
          event_type: transcript.eventType,
          metadata: { realtime_event_type: event.type },
        }).catch(() => {});
      }
    }
    const functionCall = realtimeFunctionCallFromEvent(event);
    if (functionCall) {
      await handleRealtimeFunctionCall(call, functionCall);
    }
  }

  function realtimeTranscriptFromEvent(event) {
    const eventType = text(event.type);
    if (eventType === "conversation.item.input_audio_transcription.completed") {
      const transcript = text(event.transcript);
      return transcript ? { speaker: "operator", text: transcript, eventType } : null;
    }
    if (
      eventType === "response.audio_transcript.done" ||
      eventType === "response.output_audio_transcript.done"
    ) {
      const transcript = text(event.transcript);
      return transcript ? { speaker: "agent", text: transcript, eventType } : null;
    }
    return null;
  }

  function realtimeFunctionCallFromEvent(event) {
    if (event.type === "response.function_call_arguments.done") {
      return {
        callId: text(event.call_id),
        name: text(event.name),
        argumentsText: text(event.arguments),
      };
    }
    if (event.type === "response.output_item.done" && event.item?.type === "function_call") {
      return {
        callId: text(event.item.call_id),
        name: text(event.item.name),
        argumentsText: text(event.item.arguments),
      };
    }
    return null;
  }

  async function handleRealtimeFunctionCall(call, functionCall) {
    if (!functionCall.callId || call.handledFunctionCalls.has(functionCall.callId)) {
      return;
    }
    call.handledFunctionCalls.add(functionCall.callId);
    if (functionCall.name !== "query_origin_agent") {
      sendRealtimeFunctionOutput(call, functionCall.callId, {
        status: "failed",
        error: `Unsupported function ${functionCall.name || "<empty>"}.`,
      });
      return;
    }
    const args = parseRealtimeFunctionArguments(functionCall.argumentsText);
    const question = text(args.question || args.query);
    if (!question) {
      sendRealtimeFunctionOutput(call, functionCall.callId, {
        status: "failed",
        error: "question is required",
      });
      return;
    }
    const previousStatus = call.status;
    call.status = "querying";
    peers.people.renderAgents();
    peers.chat.renderChat();
    try {
      const query = await createAgentVoiceQuery(call, {
        question,
        reason: text(args.reason),
        functionCallId: functionCall.callId,
      });
      const completed = await waitForAgentVoiceQuery(call.voiceSessionId, query.query_id);
      sendRealtimeFunctionOutput(call, functionCall.callId, {
        status: completed.status,
        answer: completed.answer,
        error: completed.error,
        citations: completed.citations || [],
        confidence: completed.confidence || null,
      });
    } catch (error) {
      sendRealtimeFunctionOutput(call, functionCall.callId, {
        status: "failed",
        error: error instanceof Error ? error.message : "origin agent query failed",
      });
    } finally {
      if (!agentVoiceCallIsTerminal(call)) {
        call.status = previousStatus === "connecting" ? "active" : previousStatus;
        peers.people.renderAgents();
        peers.chat.renderChat();
      }
    }
  }

  function parseRealtimeFunctionArguments(value) {
    try {
      const parsed = JSON.parse(value || "{}");
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch {
      return {};
    }
  }

  async function createAgentVoiceQuery(call, { question, reason, functionCallId }) {
    const response = await peers.access.teamFetch(
      `${API_BASE}/voice-sessions/${encodeURIComponent(call.voiceSessionId)}/queries`,
      {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          question,
          transcript_excerpt: agentVoiceTranscriptExcerpt(call),
          deadline_seconds: 90,
          metadata: {
            reason: reason || null,
            realtime_call_id: functionCallId,
            source: "browser_realtime_function",
          },
        }),
      },
    );
    if (!response.ok) {
      throw new Error(await responseText(response));
    }
    return response.json();
  }

  async function waitForAgentVoiceQuery(voiceSessionId, queryId) {
    const startedAt = Date.now();
    const timeoutMs = 90000;
    while (Date.now() - startedAt < timeoutMs) {
      const response = await peers.access.teamFetch(
        `${API_BASE}/voice-sessions/${encodeURIComponent(voiceSessionId)}/queries/${encodeURIComponent(queryId)}`,
        { headers: { Accept: "application/json" } },
      );
      if (!response.ok) {
        throw new Error(await responseText(response));
      }
      const payload = await response.json();
      if (["answered", "failed", "timeout"].includes(payload.status)) {
        return payload;
      }
      await delay(1200);
    }
    const timeoutResponse = await peers.access.teamFetch(
      `${API_BASE}/voice-sessions/${encodeURIComponent(voiceSessionId)}/queries/${encodeURIComponent(queryId)}/timeout`,
      {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ error: "Browser voice query timed out." }),
      },
    );
    if (!timeoutResponse.ok) {
      throw new Error(await responseText(timeoutResponse));
    }
    return timeoutResponse.json();
  }

  function sendRealtimeFunctionOutput(call, callId, output) {
    if (!call.dataChannel || call.dataChannel.readyState !== "open") {
      return;
    }
    call.dataChannel.send(
      JSON.stringify({
        type: "conversation.item.create",
        item: {
          type: "function_call_output",
          call_id: callId,
          output: JSON.stringify(output),
        },
      }),
    );
    call.dataChannel.send(JSON.stringify({ type: "response.create" }));
  }

  function agentVoiceTranscriptExcerpt(call, { limit = 12 } = {}) {
    return (call.transcript || [])
      .slice(-limit)
      .map((entry) => `${text(entry.speaker) || "voice"}: ${entry.text}`)
      .join("\n");
  }
  function handleStartVoiceCallClick(event) {

      const agentCallButton = event.target.closest("[data-agent-call-agent-id]");
      if (agentCallButton) {
        event.preventDefault();
        if (!peers.access.guardTeamMutation("start voice calls")) {
          return true;
        }
        void startAgentVoiceCall(agentCallButton, agentCallButton.dataset.agentCallAgentId);
        return true;
      }
    return false;
  }
  function handleVoiceControlsClick(event) {

      const agentCallMuteButton = event.target.closest("[data-agent-call-mute]");
      if (agentCallMuteButton) {
        event.preventDefault();
        if (!peers.access.guardTeamMutation("change voice calls")) {
          return true;
        }
        toggleAgentVoiceMute(agentCallMuteButton);
        return true;
      }

      const agentCallEndButton = event.target.closest("[data-agent-call-end]");
      if (agentCallEndButton) {
        event.preventDefault();
        if (!peers.access.guardTeamMutation("end voice calls")) {
          return true;
        }
        void endAgentVoiceCall(agentCallEndButton);
        return true;
      }
    return false;
  }

  return {
    get activeAgentVoiceCall() { return activeAgentVoiceCall; },
    agentVoiceCallIsTerminal,
    agentVoiceCallPillClass,
    agentVoiceCallStatusLabel,
    handleVoiceControlsClick,
    handleStartVoiceCallClick,
    renderAgentDetailField,
    renderAgentVoiceCallPanel,
    renderOpenAgentChatButton,
    renderOpenAgentSessionButton,
  };
}
