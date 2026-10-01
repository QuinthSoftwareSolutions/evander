// Chat scope, requests, streams, notifications, attachments, and composer drafts.
import { emptyChatState } from "./team-ui-state.js";
import {
  API_BASE,
  chatBottomStickinessPx,
  chatMessagePageSize,
  chatNotificationSoundStorageKey,
  chatNotificationSoundThrottleMs,
  chatReadStorageKey,
  chatTopLoadThresholdPx,
  initialChatDeepLink,
} from "./team-ui-config.js";
import {
  byId,
  escapeAttr,
  escapeHtml,
  humanizeToken,
  responseText,
  text,
} from "./team-ui-format.js";
import { elements } from "./team-ui-dom.js";
import { renderIdentifierText } from "./team-identifiers.js";

export function createTeamChatController(peers) {

  let chatState = emptyChatState();

  let chatRequestId = 0;

  let chatNewPickerOpen = false;

  let selectedChatOperatorUserId = null;

  let chatSending = false;

  const pendingChatAttachments = new Map();

  const chatComposerDrafts = new Map();

  let chatStream = null;

  let chatStreamConversationId = null;

  let chatStreamErrorRefreshPending = false;

  let chatNotificationSoundMuted = readChatNotificationSoundMuted();

  let chatNotificationAudioContext = null;

  let lastChatNotificationSoundAt = 0;

  let chatReadMarkers = readChatReadMarkers();

  let selectedChatConversationId = initialChatDeepLink.conversationId || null;

  let chatMobileConversationListOpen = !selectedChatConversationId;

  let pendingChatScroll = null;

  const chatOlderMessageLoads = new Set();

  async function loadChatView({
    force = false,
    preserveForms = false,
    loadGeneration = peers.app.teamViewCacheGeneration,
  } = {}) {
    if (!force && peers.app.hasLoadedView("chat")) {
      peers.app.renderShell();
      renderChat();
      syncChatStreamForActiveView();
      return null;
    }
    const requestId = ++chatRequestId;
    chatState = { ...chatState, loading: true, error: null };
    if (peers.app.activeTeamView === "chat") {
      const drafts = preserveForms ? peers.drafts.captureFormDrafts() : null;
      scheduleChatScrollForRender(selectedChatConversationId);
      renderChat();
      if (drafts) {
        peers.drafts.restoreFormDrafts(drafts);
      }
    }
    try {
      const query = new URLSearchParams({ include_selected_messages: "1" });
      if (selectedChatConversationId) {
        query.set("selected_conversation_id", selectedChatConversationId);
      }
      const response = await peers.access.teamFetch(`${API_BASE}/views/chat?${query.toString()}`, {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        throw new Error(await responseText(response));
      }
      const payload = await response.json();
      if (requestId !== chatRequestId) {
        return payload;
      }
      if (loadGeneration !== peers.app.teamViewCacheGeneration) {
        return payload;
      }
      const drafts = preserveForms && peers.app.activeTeamView === "chat" ? peers.drafts.captureFormDrafts() : null;
      peers.app.applyViewPayload(payload);
      applyChatCollectionPayload(payload.data?.chat || {}, requestId, {
        selectedConversationId: selectedChatConversationId,
        preserveSelection: true,
      });
      peers.app.markViewLoaded("chat");
      if (drafts) {
        peers.drafts.restoreFormDrafts(drafts);
      }
      return payload;
    } catch (error) {
      if (requestId !== chatRequestId) {
        return null;
      }
      chatState = {
        ...chatState,
        loading: false,
        error: error instanceof Error ? error.message : "Chat view failed to load.",
      };
      if (peers.app.activeTeamView === "chat") {
        const drafts = preserveForms ? peers.drafts.captureFormDrafts() : null;
        renderChat();
        if (drafts) {
          peers.drafts.restoreFormDrafts(drafts);
        }
      }
      throw error;
    }
  }

  function normalizeChat(payload) {
    const normalized = { ...emptyChatState(), ...(payload || {}) };
    normalized.eligible_agents = Array.isArray(normalized.eligible_agents)
      ? normalized.eligible_agents
      : [];
    normalized.operator_options = Array.isArray(normalized.operator_options)
      ? normalized.operator_options.map(normalizeChatOperatorOption).filter((item) => item.user_id)
      : [];
    normalized.current_operator = normalizeChatOperatorOption(normalized.current_operator);
    normalized.selected_operator = normalizeChatOperatorOption(normalized.selected_operator);
    normalized.can_view_all_operators = Boolean(normalized.can_view_all_operators);
    normalized.read_only = Boolean(normalized.read_only);
    normalized.conversations = Array.isArray(normalized.conversations)
      ? normalized.conversations.map(normalizeChatConversation)
      : [];
    return normalized;
  }

  function normalizeChatOperatorOption(operator) {
    const userId = text(operator?.user_id);
    const displayName = text(operator?.display_name) || userId;
    return {
      user_id: userId,
      display_name: displayName,
    };
  }

  function syncTeamStateFromChatPayload(payload) {
    if (!payload || payload.revision === undefined || payload.revision === null) {
      return;
    }
    const conversations = { ...(peers.app.state.conversations || {}) };
    const conversationMessages = { ...(peers.app.state.conversation_messages || {}) };
    const mergeConversation = (conversation) => {
      if (!conversation?.id) {
        return;
      }
      const { messages, agent: _agent, ...conversationPayload } = conversation;
      conversations[conversation.id] = {
        ...(conversations[conversation.id] || {}),
        ...conversationPayload,
      };
      if (Array.isArray(messages)) {
        messages.forEach((message) => {
          if (message?.id) {
            conversationMessages[message.id] = {
              ...(conversationMessages[message.id] || {}),
              ...message,
            };
          }
        });
      }
    };
    if (Array.isArray(payload.conversations)) {
      payload.conversations.forEach(mergeConversation);
    }
    mergeConversation(payload.conversation);
    const messagePayload = payload.message || payload.conversation_message;
    if (messagePayload?.id) {
      conversationMessages[messagePayload.id] = {
        ...(conversationMessages[messagePayload.id] || {}),
        ...messagePayload,
      };
    }
    peers.app.replaceState({
      ...peers.app.state,
      revision: peers.app.state.revision,
      conversations,
      conversation_messages: conversationMessages,
    });
  }

  function chatApiUrl(path, query = null) {
    const params = query instanceof URLSearchParams ? new URLSearchParams(query) : new URLSearchParams();
    const operatorUserId = text(selectedChatOperatorUserId);
    if (operatorUserId) {
      params.set("operator_user_id", operatorUserId);
    }
    const suffix = params.toString();
    return `${API_BASE}${path}${suffix ? `?${suffix}` : ""}`;
  }

  function chatScopeFields(payload) {
    return {
      current_operator: payload?.current_operator ?? chatState.current_operator,
      selected_operator: payload?.selected_operator ?? chatState.selected_operator,
      can_view_all_operators: payload?.can_view_all_operators ?? chatState.can_view_all_operators,
      read_only: payload?.read_only ?? chatState.read_only,
      operator_options: payload?.operator_options ?? chatState.operator_options,
    };
  }

  async function loadConversations({ selectedConversationId = null, preserveSelection = true } = {}) {
    const requestId = ++chatRequestId;
    const candidateId = selectedConversationId || (preserveSelection ? selectedChatConversationId : null);
    chatState = { ...chatState, loading: true, error: null };
    scheduleChatScrollForRender(candidateId);
    renderChat();
    try {
      const query = new URLSearchParams({ include_selected_messages: "1" });
      if (candidateId) {
        query.set("selected_conversation_id", candidateId);
      }
      const response = await peers.access.teamFetch(chatApiUrl("/conversations", query), {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        throw new Error(await responseText(response));
      }
      const payload = await response.json();
      if (requestId !== chatRequestId) {
        return;
      }
      applyChatCollectionPayload(payload, requestId, {
        selectedConversationId,
        preserveSelection,
        candidateId,
      });
      peers.app.markViewLoaded("chat");
    } catch (error) {
      if (requestId !== chatRequestId) {
        return;
      }
      chatState = {
        ...chatState,
        loading: false,
        error: error instanceof Error ? error.message : "Conversations failed to load.",
      };
      renderChat();
    }
  }

  function applyChatCollectionPayload(
    payload,
    requestId,
    { selectedConversationId = null, preserveSelection = true, candidateId = null } = {},
  ) {
    if (requestId !== chatRequestId) {
      return;
    }
    const previousSelectedConversationId = text(selectedChatConversationId);
    const previousConversations = new Map(
      chatState.conversations.map((conversation) => [text(conversation.id), conversation]),
    );
    rememberCurrentChatComposerDraft();
    chatState = normalizeChat(payload);
    maybePlayChatCollectionNotificationSound(previousConversations, chatState.conversations);
    selectedChatOperatorUserId = text(chatState.selected_operator?.user_id) || selectedChatOperatorUserId;
    syncStateAgentsFromChat(chatState);
    const responseCandidateId = selectedConversationId || (preserveSelection ? selectedChatConversationId : null);
    const fallbackCandidateId = candidateId || responseCandidateId;
    selectedChatConversationId =
      selectExistingConversationId(responseCandidateId) ||
      selectExistingConversationId(text(payload.selected_conversation_id) || fallbackCandidateId) ||
      firstConversationId();
    const selectedMessagesLoaded = chatConversationHasLoadedMessages(selectedChatConversationId);
    if (
      selectedMessagesLoaded &&
      (previousSelectedConversationId !== text(selectedChatConversationId) ||
        !chatMessageListElement(selectedChatConversationId))
    ) {
      scheduleChatScrollToBottom(selectedChatConversationId);
    }
    if (peers.app.activeTeamView === "chat") {
      peers.app.renderShell();
      renderChat();
    }
    if (selectedChatConversationId) {
      if (chatStreamShouldBeOpen()) {
        ensureChatStream(selectedChatConversationId);
        if (!selectedMessagesLoaded) {
          void loadConversationDetail(selectedChatConversationId, requestId);
        }
      } else {
        closeChatStream();
      }
    } else {
      closeChatStream();
    }
  }

  function syncStateAgentsFromChat(chatPayload) {
    const agents = { ...(peers.app.state.agents || {}) };
    (chatPayload.eligible_agents || []).forEach((agent) => {
      if (agent?.id) {
        agents[agent.id] = { ...(agents[agent.id] || {}), ...agent };
      }
    });
    (chatPayload.conversations || []).forEach((conversation) => {
      const agent = conversation?.agent;
      if (agent?.id) {
        agents[agent.id] = { ...(agents[agent.id] || {}), ...agent };
      }
    });
    peers.app.replaceState({ ...peers.app.state, revision: chatPayload.revision ?? peers.app.state.revision, agents });
  }

  async function loadConversationDetail(
    conversationId,
    requestId = chatRequestId,
    { preserveScroll = false } = {},
  ) {
    const normalizedConversationId = text(conversationId);
    if (!normalizedConversationId) {
      return;
    }
    try {
      const response = await peers.access.teamFetch(chatApiUrl(`/conversations/${encodeURIComponent(normalizedConversationId)}`), {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        throw new Error(await responseText(response));
      }
      const payload = await response.json();
      if (requestId !== chatRequestId) {
        return;
      }
      replaceChatConversation(payload.conversation);
      chatState = { ...chatState, loading: false, error: null, revision: payload.revision ?? chatState.revision };
      if (peers.app.activeTeamView === "chat" && text(selectedChatConversationId) === normalizedConversationId) {
        markChatConversationRead(normalizedConversationId, { rerender: false });
      }
      const shouldStickToBottom =
        !preserveScroll || chatIsScrolledNearBottom(normalizedConversationId);
      const scrollSnapshot = shouldStickToBottom
        ? null
        : captureChatScroll(normalizedConversationId);
      if (shouldStickToBottom) {
        scheduleChatScrollToBottom(normalizedConversationId);
      } else if (scrollSnapshot) {
        scheduleChatScrollPreserve(normalizedConversationId, scrollSnapshot);
      }
      renderChat();
    } catch (error) {
      if (requestId !== chatRequestId) {
        return;
      }
      chatState = {
        ...chatState,
        loading: false,
        error: error instanceof Error ? error.message : "Conversation failed to load.",
      };
      renderChat();
    }
  }

  async function loadOlderChatMessages(conversationId) {
    const normalizedConversationId = text(conversationId);
    const conversation = chatConversationById(normalizedConversationId);
    if (!conversation || !conversation.has_older || chatOlderMessageLoads.has(normalizedConversationId)) {
      return;
    }
    const messages = conversationMessages(conversation);
    const beforeMessageId = text(conversation.oldest_message_id) || text(messages[0]?.id);
    if (!beforeMessageId) {
      return;
    }
    const scrollSnapshot = captureChatScroll(normalizedConversationId);
    chatOlderMessageLoads.add(normalizedConversationId);
    if (scrollSnapshot) {
      scheduleChatScrollPreserve(normalizedConversationId, scrollSnapshot);
    }
    renderChat();
    try {
      const query = new URLSearchParams({
        before: beforeMessageId,
        limit: String(chatMessagePageSize),
      });
      const response = await peers.access.teamFetch(
        chatApiUrl(`/conversations/${encodeURIComponent(normalizedConversationId)}/messages`, query),
        { headers: { Accept: "application/json" } },
      );
      if (!response.ok) {
        throw new Error(await responseText(response));
      }
      const payload = await response.json();
      if (text(payload.conversation_id) !== normalizedConversationId) {
        return;
      }
      applyChatMessagePage(normalizedConversationId, payload);
      chatState = { ...chatState, revision: payload.revision ?? chatState.revision };
      if (scrollSnapshot) {
        scheduleChatScrollPreservePrepend(normalizedConversationId, scrollSnapshot);
      }
    } catch (error) {
      chatState = {
        ...chatState,
        error: error instanceof Error ? error.message : "Older messages failed to load.",
      };
    } finally {
      chatOlderMessageLoads.delete(normalizedConversationId);
      renderChat();
    }
  }

  function selectExistingConversationId(conversationId) {
    const normalizedConversationId = text(conversationId);
    if (!normalizedConversationId) {
      return null;
    }
    return chatState.conversations.some((conversation) => text(conversation.id) === normalizedConversationId)
      ? normalizedConversationId
      : null;
  }

  function firstConversationId() {
    return text(chatState.conversations[0]?.id) || null;
  }

  function chatConversationHasLoadedMessages(conversationId) {
    const conversation = chatConversationById(conversationId);
    return conversation?.messages_loaded === true;
  }

  function replaceChatConversation(conversation) {
    if (!conversation?.id) {
      return;
    }
    const existing = chatConversationById(conversation.id);
    const nextConversation = mergeChatConversation(existing, conversation);
    const nextConversations = chatState.conversations.filter((item) => text(item.id) !== text(conversation.id));
    nextConversations.push(nextConversation);
    nextConversations.sort(compareChatConversations);
    chatState = { ...chatState, conversations: nextConversations };
  }

  function chatStreamShouldBeOpen() {
    return peers.app.activeTeamView === "chat" && document.visibilityState !== "hidden";
  }

  function syncChatStreamForActiveView({ refresh = false } = {}) {
    if (!chatStreamShouldBeOpen()) {
      closeChatStream();
      return;
    }
    if (!selectedChatConversationId) {
      return;
    }
    ensureChatStream(selectedChatConversationId);
    if (refresh) {
      void loadConversationDetail(selectedChatConversationId, chatRequestId, { preserveScroll: true });
    }
  }

  function ensureChatStream(conversationId) {
    const normalizedConversationId = text(conversationId);
    if (!normalizedConversationId || typeof EventSource === "undefined") {
      return;
    }
    if (!chatStreamShouldBeOpen()) {
      closeChatStream();
      return;
    }
    if (chatStream && chatStreamConversationId === normalizedConversationId) {
      return;
    }
    closeChatStream();
    chatStreamConversationId = normalizedConversationId;
    chatStreamErrorRefreshPending = false;
    const stream = new EventSource(
      chatApiUrl(`/conversations/${encodeURIComponent(normalizedConversationId)}/stream`),
    );
    chatStream = stream;
    stream.addEventListener("snapshot", (event) => {
      if (chatStream !== stream || chatStreamConversationId !== normalizedConversationId) {
        return;
      }
      try {
        const payload = JSON.parse(event.data || "{}");
        applyChatSnapshot(payload, normalizedConversationId);
      } catch {
        // Ignore malformed stream events; EventSource will keep the connection alive.
      }
    });
    stream.onerror = () => {
      if (chatStream !== stream || chatStreamConversationId !== normalizedConversationId) {
        return;
      }
      if (chatStreamErrorRefreshPending) {
        return;
      }
      chatStreamErrorRefreshPending = true;
      void loadConversationDetail(normalizedConversationId, chatRequestId, { preserveScroll: true }).finally(() => {
        if (chatStream === stream) {
          chatStreamErrorRefreshPending = false;
        }
      });
    };
  }

  function closeChatStream() {
    if (chatStream) {
      chatStream.close();
    }
    chatStream = null;
    chatStreamConversationId = null;
    chatStreamErrorRefreshPending = false;
  }

  function applyChatSnapshot(payload, expectedConversationId) {
    const conversation = payload?.conversation;
    if (!conversation?.id || text(conversation.id) !== text(expectedConversationId)) {
      return;
    }
    const previousConversation = chatConversationById(expectedConversationId);
    chatState = normalizeChat({ ...chatState, ...chatScopeFields(payload) });
    const scrollSnapshot = captureChatScroll(expectedConversationId);
    const shouldStickToBottom =
      (pendingChatScroll?.mode === "bottom" &&
        text(pendingChatScroll.conversationId) === text(expectedConversationId)) ||
      chatIsScrolledNearBottom(expectedConversationId);
    syncTeamStateFromChatPayload(payload);
    replaceChatConversation(conversation);
    maybePlayChatNotificationSound(
      previousConversation,
      chatConversationById(expectedConversationId),
    );
    chatState = {
      ...chatState,
      loading: false,
      error: null,
      revision: payload.revision ?? chatState.revision,
    };
    if (peers.app.activeTeamView === "chat" && text(selectedChatConversationId) === text(expectedConversationId)) {
      markChatConversationRead(expectedConversationId, { rerender: false });
    }
    if (shouldStickToBottom) {
      scheduleChatScrollToBottom(expectedConversationId);
    } else if (scrollSnapshot) {
      scheduleChatScrollPreserve(expectedConversationId, scrollSnapshot);
    }
    renderChat();
  }

  async function createOrFocusChat(agentId) {
    const normalizedAgentId = text(agentId);
    if (!normalizedAgentId) {
      return;
    }
    peers.app.setStatus("Opening chat.", "loading");
    rememberCurrentChatComposerDraft();
    try {
      const response = await peers.access.teamFetch(`${API_BASE}/conversations`, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          agent_id: normalizedAgentId,
        }),
      });
      if (!response.ok) {
        throw new Error(await responseText(response));
      }
      const payload = await response.json();
      syncTeamStateFromChatPayload(payload);
      chatState = normalizeChat(payload);
      selectedChatOperatorUserId = text(chatState.selected_operator?.user_id) || selectedChatOperatorUserId;
      selectedChatConversationId = text(payload.selected_conversation_id) || firstConversationId();
      chatNewPickerOpen = false;
      chatMobileConversationListOpen = false;
      const selectedMessagesLoaded = chatConversationHasLoadedMessages(selectedChatConversationId);
      if (selectedMessagesLoaded) {
        scheduleChatScrollToBottom(selectedChatConversationId);
      }
      renderChat();
      if (selectedChatConversationId) {
        ensureChatStream(selectedChatConversationId);
        if (!selectedMessagesLoaded) {
          await loadConversationDetail(selectedChatConversationId);
        }
      }
      peers.app.setStatus("", "");
    } catch (error) {
      peers.app.setStatus(error instanceof Error ? error.message : "Chat could not be opened.", "error");
    }
  }

  async function openChatForAgent(agentId) {
    const normalizedAgentId = text(agentId);
    if (!normalizedAgentId) {
      return;
    }
    if (chatState.read_only) {
      peers.app.setStatus("This chat history is read-only.", "error");
      return;
    }
    peers.app.setTeamView("chat");
    try {
      await peers.app.loadView("chat");
    } catch (error) {
      peers.app.setStatus(error instanceof Error ? error.message : "Chat view failed to load.", "error");
      return;
    }
    if (chatState.read_only) {
      peers.app.setStatus("This chat history is read-only.", "error");
      return;
    }
    await createOrFocusChat(normalizedAgentId);
  }

  async function submitChatMessage(form) {
    const conversation = selectedChatConversation();
    if (!conversation || chatSending) {
      return;
    }
    if (chatState.read_only || conversation.read_only) {
      peers.app.setStatus("This chat history is read-only.", "error");
      return;
    }
    const formData = new FormData(form);
    const body = text(formData.get("body"));
    const attachments = [...(pendingChatAttachments.get(text(conversation.id)) || [])];
    if (!body && !attachments.length) {
      peers.app.setStatus("Message or attachment is required.", "error");
      return;
    }
    chatSending = true;
    scheduleChatScrollForRender(conversation.id);
    renderChat();
    peers.app.setStatus("Sending chat message.", "loading");
    try {
      const conversationId = text(conversation.id);
      let requestBody = JSON.stringify({ body });
      const requestHeaders = { Accept: "application/json", "Content-Type": "application/json" };
      if (attachments.length) {
        requestBody = new FormData();
        requestBody.set("body", body);
        attachments.forEach((file) => requestBody.append("files", file, file.name));
        delete requestHeaders["Content-Type"];
      }
      const response = await peers.access.teamFetch(`${API_BASE}/conversations/${encodeURIComponent(conversation.id)}/messages`, {
        method: "POST",
        headers: requestHeaders,
        body: requestBody,
      });
      if (!response.ok) {
        throw new Error(await responseText(response));
      }
      const payload = await response.json();
      syncTeamStateFromChatPayload(payload);
      chatState = normalizeChat({ ...chatState, ...chatScopeFields(payload) });
      selectedChatOperatorUserId = text(chatState.selected_operator?.user_id) || selectedChatOperatorUserId;
      replaceChatConversation(payload.conversation);
      selectedChatConversationId = payload.conversation?.id || selectedChatConversationId;
      chatState = { ...chatState, revision: payload.revision ?? chatState.revision };
      if (selectedChatConversationId) {
        ensureChatStream(selectedChatConversationId);
      }
      const activeForm = elements.chatThreadView?.querySelector("[data-chat-message-form]");
      const activeConversationId = text(
        activeForm?.closest(".chat-thread")?.querySelector("[data-chat-scroll-conversation-id]")
          ?.dataset?.chatScrollConversationId,
      );
      const activeComposer = activeForm?.querySelector('textarea[name="body"]');
      if (text(activeConversationId) === conversationId) {
        if (activeComposer instanceof HTMLTextAreaElement) {
          activeComposer.value = "";
        }
        pendingChatAttachments.delete(conversationId);
      }
      chatComposerDrafts.delete(conversationId);
      if (payload.dispatch?.success === false && !payload.dispatch?.queued) {
        peers.app.setStatus(payload.dispatch.message || "Chat message saved, but dispatch failed.", "warning");
      } else {
        peers.app.setStatus("", "");
      }
      if (selectedChatConversationId) {
        scheduleChatScrollToBottom(selectedChatConversationId);
      }
    } catch (error) {
      peers.app.setStatus(error instanceof Error ? error.message : "Chat message failed.", "error");
    } finally {
      chatSending = false;
      renderChat();
    }
  }

  async function openChatAttachmentPolicy() {
    const form = elements.chatAttachmentPolicyForm;
    if (!form || !elements.chatAttachmentPolicyModal) return;
    const directoryPath = text(form.elements.directory_path?.value) || "root";
    peers.app.setStatus("Loading Team Chat attachment policy.", "loading");
    try {
      const response = await peers.access.teamFetch(
        `${API_BASE}/chat/attachment-policy?directory_path=${encodeURIComponent(directoryPath)}`,
        { headers: { Accept: "application/json" } },
      );
      if (!response.ok) throw new Error(await responseText(response));
      const payload = await response.json();
      fillChatAttachmentPolicyForm(form, payload.effective || {}, payload.directory_path || directoryPath);
      elements.chatAttachmentPolicyModal.hidden = false;
      form.elements.directory_path?.focus();
      peers.app.setStatus("", "");
    } catch (error) {
      peers.app.setStatus(error instanceof Error ? error.message : "Attachment policy could not be loaded.", "error");
    }
  }

  function closeChatAttachmentPolicy() {
    if (elements.chatAttachmentPolicyModal) elements.chatAttachmentPolicyModal.hidden = true;
  }

  function fillChatAttachmentPolicyForm(form, policy, directoryPath) {
    form.elements.directory_path.value = directoryPath || "root";
    form.elements.operator_to_agent_enabled.checked = Boolean(policy.operator_to_agent_enabled);
    form.elements.agent_to_operator_enabled.checked = Boolean(policy.agent_to_operator_enabled);
    form.elements.agent_send_requires_approval.checked = Boolean(policy.agent_send_requires_approval);
    form.elements.max_attachment_bytes.value = policy.max_attachment_bytes ?? 10485760;
    form.elements.max_attachments_per_message.value = policy.max_attachments_per_message ?? 5;
    form.elements.allowed_mime_types.value = (policy.allowed_mime_types || []).join("\n");
    form.elements.allowed_extensions.value = (policy.allowed_extensions || []).join("\n");
    form.elements.blocked_extensions.value = (policy.blocked_extensions || []).join("\n");
  }

  function chatAttachmentPolicyList(value) {
    return String(value || "").split(/[\n,]+/).map(text).filter(Boolean);
  }

  async function submitChatAttachmentPolicy(form) {
    if (!peers.access.guardTeamMutation("configure Team Chat attachments")) return;
    const directoryPath = text(form.elements.directory_path.value) || "root";
    const policy = {
      operator_to_agent_enabled: form.elements.operator_to_agent_enabled.checked,
      agent_to_operator_enabled: form.elements.agent_to_operator_enabled.checked,
      agent_send_requires_approval: form.elements.agent_send_requires_approval.checked,
      max_attachment_bytes: Number(form.elements.max_attachment_bytes.value),
      max_attachments_per_message: Number(form.elements.max_attachments_per_message.value),
      allowed_mime_types: chatAttachmentPolicyList(form.elements.allowed_mime_types.value),
      allowed_extensions: chatAttachmentPolicyList(form.elements.allowed_extensions.value),
      blocked_extensions: chatAttachmentPolicyList(form.elements.blocked_extensions.value),
    };
    peers.app.setStatus("Saving Team Chat attachment policy.", "loading");
    try {
      const response = await peers.access.teamFetch(`${API_BASE}/chat/attachment-policy`, {
        method: "PUT",
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: JSON.stringify({
          directory_path: directoryPath,
          expected_revision: Number(chatState.revision ?? peers.app.state.revision),
          policy,
        }),
      });
      if (!response.ok) throw new Error(await responseText(response));
      const payload = await response.json();
      peers.app.state.revision = payload.revision ?? peers.app.state.revision;
      chatState.revision = payload.revision ?? chatState.revision;
      fillChatAttachmentPolicyForm(form, payload.effective || policy, directoryPath);
      closeChatAttachmentPolicy();
      peers.app.renderShell();
      peers.app.setStatus("Team Chat attachment policy saved.", "success");
    } catch (error) {
      peers.app.setStatus(error instanceof Error ? error.message : "Attachment policy could not be saved.", "error");
    }
  }

  function renderChat() {
    renderChatOperatorView();
    renderChatConversationList();
    renderChatThread();
    syncChatMobilePane();
    peers.app.updateTeamViewTabs();
    peers.access.applyLockedMutationControls();
  }

  function syncChatMobilePane() {
    if (!elements.chatPage) {
      return;
    }
    const showConversationList = chatMobileConversationListOpen || !selectedChatConversation();
    elements.chatPage.classList.toggle("is-conversation-list-open", showConversationList);
  }

  function readChatReadMarkers() {
    try {
      const parsed = JSON.parse(window.localStorage.getItem(chatReadStorageKey) || "{}");
      return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
    } catch {
      return {};
    }
  }

  function storeChatReadMarkers() {
    try {
      window.localStorage.setItem(chatReadStorageKey, JSON.stringify(chatReadMarkers));
    } catch {
      // Storage can be unavailable in hardened browser contexts.
    }
  }

  function readChatNotificationSoundMuted() {
    try {
      return window.localStorage.getItem(chatNotificationSoundStorageKey) === "true";
    } catch {
      return false;
    }
  }

  function storeChatNotificationSoundMuted() {
    try {
      window.localStorage.setItem(chatNotificationSoundStorageKey, String(chatNotificationSoundMuted));
    } catch {
      // Storage can be unavailable in hardened browser contexts.
    }
  }

  function maybePlayChatNotificationSound(previousConversation, nextConversation) {
    if (chatNotificationSoundMuted || chatWindowIsFocused()) {
      return;
    }
    if (!previousConversation || !nextConversation) {
      return;
    }
    const previousNewestMessageId = chatConversationNewestMessageId(previousConversation);
    const nextNewestMessageId = chatConversationNewestMessageId(nextConversation);
    if (!nextNewestMessageId || previousNewestMessageId === nextNewestMessageId) {
      return;
    }
    if (chatConversationNewestSender(nextConversation) === "operator") {
      return;
    }
    const now = Date.now();
    if (now - lastChatNotificationSoundAt < chatNotificationSoundThrottleMs) {
      return;
    }
    lastChatNotificationSoundAt = now;
    void playChatNotificationSound().catch(() => {
      // Browsers may block audio until the operator has interacted with the page.
    });
  }

  function maybePlayChatCollectionNotificationSound(previousConversations, nextConversations) {
    if (!(previousConversations instanceof Map) || !Array.isArray(nextConversations)) {
      return;
    }
    nextConversations.forEach((conversation) => {
      const conversationId = text(conversation?.id);
      if (conversationId) {
        maybePlayChatNotificationSound(previousConversations.get(conversationId), conversation);
      }
    });
  }

  function chatWindowIsFocused() {
    return typeof document.hasFocus === "function"
      ? document.hasFocus()
      : document.visibilityState === "visible";
  }

  async function playChatNotificationSound() {
    const AudioContextConstructor = window.AudioContext || window.webkitAudioContext;
    if (typeof AudioContextConstructor !== "function") {
      return;
    }
    if (!chatNotificationAudioContext || chatNotificationAudioContext.state === "closed") {
      chatNotificationAudioContext = new AudioContextConstructor();
    }
    if (chatNotificationAudioContext.state === "suspended") {
      await chatNotificationAudioContext.resume();
    }
    const audioTime = chatNotificationAudioContext.currentTime;
    const oscillator = chatNotificationAudioContext.createOscillator();
    const gain = chatNotificationAudioContext.createGain();
    oscillator.type = "sine";
    oscillator.frequency.setValueAtTime(880, audioTime);
    oscillator.frequency.exponentialRampToValueAtTime(1320, audioTime + 0.08);
    gain.gain.setValueAtTime(0.0001, audioTime);
    gain.gain.exponentialRampToValueAtTime(0.12, audioTime + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, audioTime + 0.18);
    oscillator.connect(gain);
    gain.connect(chatNotificationAudioContext.destination);
    oscillator.onended = () => {
      oscillator.disconnect();
      gain.disconnect();
    };
    oscillator.start(audioTime);
    oscillator.stop(audioTime + 0.2);
  }

  function chatConversationNewestMessageId(conversation) {
    return text(conversation?.newest_message_id) || text(conversationMessages(conversation).at(-1)?.id);
  }

  function chatConversationNewestSender(conversation) {
    return text(conversation?.newest_message_sender) || text(conversationMessages(conversation).at(-1)?.sender);
  }

  function chatConversationHasUnread(conversation) {
    const conversationId = text(conversation?.id);
    const newestMessageId = chatConversationNewestMessageId(conversation);
    const newestSender = chatConversationNewestSender(conversation);
    if (!conversationId || !newestMessageId || newestSender === "operator") {
      return false;
    }
    return text(chatReadMarkers[conversationId]) !== newestMessageId;
  }

  function unreadChatConversationCount() {
    return chatState.conversations.filter(chatConversationHasUnread).length;
  }

  function markChatConversationRead(conversationId, { rerender = true } = {}) {
    const normalizedConversationId = text(conversationId);
    if (!normalizedConversationId) {
      return false;
    }
    const conversation = chatConversationById(normalizedConversationId);
    const newestMessageId = chatConversationNewestMessageId(conversation);
    const newestSender = chatConversationNewestSender(conversation);
    if (newestMessageId && newestSender !== "operator") {
      markAdminChatConversationNotificationsRead(normalizedConversationId);
    }
    if (!newestMessageId || text(chatReadMarkers[normalizedConversationId]) === newestMessageId) {
      return false;
    }
    chatReadMarkers = {
      ...chatReadMarkers,
      [normalizedConversationId]: newestMessageId,
    };
    storeChatReadMarkers();
    if (rerender) {
      renderChat();
    }
    return true;
  }

  function markAdminChatConversationNotificationsRead(conversationId) {
    const normalizedConversationId = text(conversationId);
    if (!normalizedConversationId) {
      return;
    }
    void peers.access.teamFetch("/api/admin/user-notifications/read", {
      method: "POST",
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        source: "team-chat",
        metadata: {
          conversation_id: normalizedConversationId,
        },
      }),
    })
      .then((response) => {
        if (response.ok && window.parent && window.parent !== window) {
          window.parent.postMessage({ type: "admin-user-notifications:changed" }, window.location.origin);
        }
      })
      .catch(() => {});
  }

  function renderChatOperatorView() {
    if (!elements.chatOperatorView) {
      return;
    }
    const current = chatState.current_operator || {};
    const selected = chatState.selected_operator || current;
    const currentUserId = text(current.user_id);
    const selectedUserId = text(selected.user_id) || text(selectedChatOperatorUserId) || currentUserId;
    const selectedLabel = text(selected.display_name) || selectedUserId || "Operator";
    const options = chatState.operator_options.length
      ? chatState.operator_options
      : selectedUserId
        ? [selected]
        : [];
    const modeLabel = chatState.read_only ? "Read-only inspection" : "My chats";
    elements.chatOperatorView.innerHTML = `
      <div class="chat-operator-scope">
        <span class="state-pill ${chatState.read_only ? "paused" : "active"}">${escapeHtml(modeLabel)}</span>
        ${
          chatState.can_view_all_operators && options.length
            ? `
              <label>
                <span>Operator</span>
                <select data-chat-operator-select>
                  ${options.map((option) => {
                    const value = text(option.user_id);
                    const label = text(option.display_name) || value;
                    return `<option value="${escapeAttr(value)}" ${value === selectedUserId ? "selected" : ""}>${escapeHtml(label)}</option>`;
                  }).join("")}
                </select>
              </label>
            `
            : `<span class="meta-line">${escapeHtml(selectedLabel)}</span>`
        }
      </div>
    `;
    document.querySelectorAll("[data-chat-new]").forEach((button) => {
      button.disabled = Boolean(chatState.read_only);
    });
  }

  function renderChatConversationList() {
    if (!elements.chatConversationsView) {
      return;
    }
    if (chatState.error && !chatState.conversations.length) {
      elements.chatConversationsView.innerHTML = `<p class="empty-state">${escapeHtml(chatState.error)}</p>`;
    } else if (chatState.loading && !chatState.conversations.length) {
      elements.chatConversationsView.innerHTML = `<p class="empty-state">Loading conversations.</p>`;
    } else if (!chatState.conversations.length) {
      elements.chatConversationsView.innerHTML = `<p class="empty-state">No conversations.</p>`;
    } else {
      elements.chatConversationsView.innerHTML = `
        <ul class="chat-conversation-list">
          ${chatState.conversations.map(renderChatConversationRow).join("")}
        </ul>
      `;
    }
    renderChatNewPicker();
  }

  function renderChatNewPicker() {
    if (!elements.chatNewPicker) {
      return;
    }
    const previousScrollTop = chatAgentPickerScrollTop();
    if (chatState.read_only) {
      elements.chatNewPicker.hidden = true;
      elements.chatNewPicker.innerHTML = "";
      return;
    }
    elements.chatNewPicker.hidden = !chatNewPickerOpen;
    if (!chatNewPickerOpen) {
      elements.chatNewPicker.innerHTML = "";
      return;
    }
    const agents = [...chatState.eligible_agents].sort((left, right) => {
      return text(left.display_name || left.id).localeCompare(text(right.display_name || right.id));
    });
    elements.chatNewPicker.innerHTML = agents.length
      ? `
        <div class="chat-agent-picker">
          ${agents.map(renderChatAgentPickerRow).join("")}
        </div>
      `
      : `<p class="empty-state">No available agents.</p>`;
    restoreChatAgentPickerScrollTop(previousScrollTop);
  }

  function chatAgentPickerScrollTop() {
    const picker = elements.chatNewPicker?.querySelector(".chat-agent-picker");
    return picker instanceof HTMLElement ? picker.scrollTop : null;
  }

  function restoreChatAgentPickerScrollTop(scrollTop) {
    if (scrollTop === null) {
      return;
    }
    const picker = elements.chatNewPicker?.querySelector(".chat-agent-picker");
    if (picker instanceof HTMLElement) {
      picker.scrollTop = scrollTop;
    }
  }

  function renderChatConversationRow(conversation) {
    const selected = text(conversation.id) === text(selectedChatConversationId);
    const unread = chatConversationHasUnread(conversation);
    const agent = conversation.agent || {};
    const label = text(agent.display_name) || text(agent.id) || text(conversation.agent_id);
    const preview = text(conversation.last_message_preview) || "No messages yet.";
    return `
      <li>
        <button
          type="button"
          class="chat-conversation-row${selected ? " is-selected" : ""}${unread ? " has-unread" : ""}"
          data-chat-conversation-id="${escapeAttr(conversation.id)}"
          aria-pressed="${selected ? "true" : "false"}"
          aria-label="${escapeAttr(`${label}${unread ? ", unread" : ""}`)}"
        >
          ${renderChatAvatar(agent)}
          <span class="chat-conversation-main">
            <strong>${escapeHtml(label)}</strong>
            <span>${escapeHtml(preview)}</span>
          </span>
          <span class="chat-conversation-meta">
            ${unread ? `<span class="chat-unread-badge" aria-hidden="true">New</span>` : ""}
            <time>${escapeHtml(formatChatTimestamp(conversation.last_interaction_at))}</time>
          </span>
        </button>
      </li>
    `;
  }

  function renderChatAgentPickerRow(agent) {
    const conversation = conversationForAgent(agent.id);
    const label = text(agent.display_name) || text(agent.id);
    const details = peers.agents.agentCardData(agent);
    return `
      <button type="button" class="chat-agent-picker-row" data-chat-agent-id="${escapeAttr(agent.id)}">
        ${renderChatAvatar(agent)}
        <span class="chat-conversation-main">
          <strong>${escapeHtml(label)}</strong>
          <span>${escapeHtml(conversation ? "Open conversation" : agent.session_id || agent.id)}</span>
        </span>
        ${peers.agents.renderAgentRosterStatusPill(details)}
      </button>
    `;
  }

  function renderChatThread() {
    if (!elements.chatThreadView) {
      return;
    }
    const conversation = selectedChatConversation();
    if (!conversation) {
      elements.chatThreadView.innerHTML = `
        <div class="chat-empty">
          <p class="empty-state">${chatState.loading ? "Loading conversations." : "Select or start a chat."}</p>
        </div>
      `;
      return;
    }
    const composerState =
      captureChatComposerState(conversation.id)
      || chatComposerDrafts.get(text(conversation.id))
      || null;
    const agent = conversation.agent || {};
    const messages = conversationMessages(conversation);
    const label = text(agent.display_name) || text(agent.id) || text(conversation.agent_id);
    const olderLoading = chatOlderMessageLoads.has(text(conversation.id));
    const callStrip = renderChatCallStrip(conversation);
    const readOnly = Boolean(chatState.read_only || conversation.read_only);
    const listSignature = chatMessageListSignature(conversation, messages, olderLoading);
    const existingThread = elements.chatThreadView.querySelector(".chat-thread");
    const canPatchThread =
      existingThread instanceof HTMLElement &&
      text(existingThread.dataset.chatThreadConversationId) === text(conversation.id) &&
      existingThread.dataset.chatThreadCallStrip === (callStrip ? "1" : "0") &&
      existingThread.dataset.chatThreadReadOnly === (readOnly ? "1" : "0");
    if (
      canPatchThread &&
      patchChatThread(existingThread, {
        conversation,
        agent,
        messages,
        label,
        olderLoading,
        callStrip,
        readOnly,
        listSignature,
      })
    ) {
      restoreChatComposerState(conversation.id, composerState);
      applyPendingChatScroll(conversation.id);
      return;
    }
    elements.chatThreadView.innerHTML = `
      ${renderChatThreadMarkup({
        conversation,
        agent,
        messages,
        label,
        olderLoading,
        callStrip,
        readOnly,
      })}
    `;
    setChatMessageListSignature(conversation.id, listSignature);
    restoreChatComposerState(conversation.id, composerState);
    bindChatMessageListScroll(conversation.id);
    applyPendingChatScroll(conversation.id);
  }

  function renderChatThreadMarkup({
    conversation,
    agent,
    messages,
    label,
    olderLoading,
    callStrip,
    readOnly,
  }) {
    return `
      <div
        class="chat-thread${callStrip ? " has-call-strip" : ""}"
        data-chat-thread-conversation-id="${escapeAttr(conversation.id)}"
        data-chat-thread-call-strip="${callStrip ? "1" : "0"}"
        data-chat-thread-read-only="${readOnly ? "1" : "0"}"
      >
        ${renderChatThreadHeader(conversation, agent, label, readOnly)}
        ${renderChatReadonlyBanner(conversation, readOnly)}
        ${callStrip}
        ${renderChatMessageList(conversation, messages, olderLoading)}
        ${renderChatComposer(conversation, label, readOnly)}
      </div>
    `;
  }

  function patchChatThread(
    thread,
    {
      conversation,
      agent,
      messages,
      label,
      olderLoading,
      callStrip,
      readOnly,
      listSignature,
    },
  ) {
    const header = thread.querySelector(".chat-thread-header");
    const list = thread.querySelector(".chat-message-list");
    const composer = thread.querySelector(".chat-composer");
    if (!header || !list || !composer) {
      return false;
    }
    header.outerHTML = renderChatThreadHeader(conversation, agent, label, readOnly);
    const banner = thread.querySelector(".chat-readonly-banner");
    if (readOnly && banner) {
      banner.outerHTML = renderChatReadonlyBanner(conversation, readOnly);
    }
    const callStripElement = thread.querySelector(".chat-call-strip");
    if (callStrip && callStripElement) {
      callStripElement.outerHTML = callStrip;
    }
    if (list.dataset.chatMessageSignature !== listSignature) {
      list.innerHTML = renderChatMessageListContents(conversation, messages, olderLoading);
      list.dataset.chatMessageSignature = listSignature;
    }
    composer.outerHTML = renderChatComposer(conversation, label, readOnly);
    return true;
  }

  function renderChatThreadHeader(conversation, agent, label, readOnly) {
    return `
      <header class="chat-thread-header">
        <div class="chat-thread-title">
          <button
            type="button"
            class="chat-mobile-conversations-button"
            data-chat-mobile-conversations
            aria-label="Back to conversations"
          >Chats</button>
          ${renderChatAvatar(agent)}
          <div>
            <h2>${escapeHtml(label)}</h2>
            <div class="meta-line">${escapeHtml(agent.session_id || conversation.agent_id || "")}</div>
          </div>
        </div>
        <div class="chat-thread-actions">
          <button type="button" data-chat-attachment-policy-open title="Configure add-on-owned file rules by directory">Files</button>
          <label
            class="chat-sound-toggle"
            title="Play a short sound when agent messages arrive and this window is not focused."
          >
            <input
              type="checkbox"
              data-chat-notification-sound
              ${chatNotificationSoundMuted ? "" : "checked"}
            />
            <span>Sound</span>
          </label>
          <button
            type="button"
            data-agent-call-agent-id="${escapeAttr(conversation.agent_id)}"
            data-agent-call-conversation-id="${escapeAttr(conversation.id)}"
            ${readOnly ? "disabled" : ""}
          >Call</button>
          <button type="button" data-chat-refresh>Refresh</button>
        </div>
      </header>
    `;
  }

  function renderChatReadonlyBanner(conversation, readOnly) {
    return readOnly
      ? `<div class="chat-readonly-banner">Viewing ${escapeHtml(conversation.operator_display_name || conversation.operator_user_id || "another operator")} read-only.</div>`
      : "";
  }

  function renderChatMessageList(conversation, messages, olderLoading) {
    return `
      <div class="chat-message-list" role="log" aria-live="polite" data-chat-scroll-conversation-id="${escapeAttr(conversation.id)}">
        ${renderChatMessageListContents(conversation, messages, olderLoading)}
      </div>
    `;
  }

  function renderChatMessageListContents(conversation, messages, olderLoading) {
    return `
      ${
        conversation.has_older || olderLoading
          ? `<div class="chat-history-sentinel" data-chat-history-sentinel>${olderLoading ? "Loading older messages." : ""}</div>`
          : ""
      }
      ${
        messages.length
          ? messages.map(renderChatMessage).join("")
          : `<p class="empty-state">No messages.</p>`
      }
    `;
  }

  function renderChatComposer(conversation, label, readOnly) {
    const conversationId = text(selectedChatConversationId);
    const attachments = [...(pendingChatAttachments.get(conversationId) || [])];
    const mutationEnabled = !readOnly && text(conversation?.mutation_mode) === "project_scoped";
    const modeLabel = mutationEnabled ? "Mutation enabled" : "Reply only";
    const modeTitle = mutationEnabled
      ? "This message can authorize Team mutations within your directory-scoped project permissions."
      : "This message can receive an agent reply but cannot authorize Team mutations.";
    return `
      <form class="chat-composer" data-chat-message-form>
        <textarea name="body" rows="3" placeholder="${readOnly ? "Read-only chat history" : `Message ${escapeAttr(label)}`}" ${chatSending || readOnly ? "disabled" : ""}></textarea>
        <div class="chat-attachment-drafts">
          ${attachments.map((file, index) => `
            <span class="chat-attachment-chip">
              <span>${escapeHtml(file.name)} · ${escapeHtml(formatFileSize(file.size))}</span>
              <button type="button" aria-label="Remove ${escapeAttr(file.name)}" data-chat-attachment-remove="${index}" data-chat-attachment-conversation-id="${escapeAttr(conversationId)}">×</button>
            </span>
          `).join("")}
        </div>
        <div class="chat-composer-actions">
          <span class="chat-mutation-mode ${mutationEnabled ? "is-enabled" : "is-reply-only"}" title="${escapeAttr(modeTitle)}">${escapeHtml(modeLabel)}</span>
          <label class="chat-attach-button ${readOnly ? "is-disabled" : ""}">
            <input type="file" multiple data-chat-attachment-input="${escapeAttr(conversationId)}" ${chatSending || readOnly ? "disabled" : ""} />
            <span>Attach</span>
          </label>
          <button type="submit" ${chatSending || readOnly ? "disabled" : ""}>Send</button>
        </div>
      </form>
    `;
  }

  function captureChatComposerState(expectedConversationId) {
    const form = elements.chatThreadView?.querySelector("[data-chat-message-form]");
    const textarea = form?.querySelector('textarea[name="body"]');
    if (!form || !(textarea instanceof HTMLTextAreaElement)) {
      return null;
    }
    const messageList = elements.chatThreadView?.querySelector("[data-chat-scroll-conversation-id]");
    if (text(messageList?.dataset.chatScrollConversationId) !== text(expectedConversationId)) {
      return null;
    }
    return {
      conversationId: text(expectedConversationId),
      value: textarea.value,
      hadFocus: document.activeElement === textarea,
      selectionStart: typeof textarea.selectionStart === "number" ? textarea.selectionStart : 0,
      selectionEnd: typeof textarea.selectionEnd === "number" ? textarea.selectionEnd : 0,
    };
  }

  function rememberCurrentChatComposerDraft() {
    const conversationId = text(
      elements.chatThreadView
        ?.querySelector("[data-chat-scroll-conversation-id]")
        ?.dataset?.chatScrollConversationId,
    );
    if (!conversationId) {
      return null;
    }
    const composerState = captureChatComposerState(conversationId);
    if (composerState) {
      const previous = chatComposerDrafts.get(conversationId);
      chatComposerDrafts.set(
        conversationId,
        previous?.hadFocus && !composerState.hadFocus
          ? {
              ...composerState,
              hadFocus: true,
              selectionStart: previous.selectionStart,
              selectionEnd: previous.selectionEnd,
            }
          : composerState,
      );
    }
    return composerState;
  }

  function restoreChatComposerState(expectedConversationId, composerState) {
    if (!composerState || composerState.conversationId !== text(expectedConversationId)) {
      return;
    }
    const form = elements.chatThreadView?.querySelector("[data-chat-message-form]");
    const textarea = form?.querySelector('textarea[name="body"]');
    if (!(textarea instanceof HTMLTextAreaElement)) {
      return;
    }
    textarea.value = composerState.value ?? "";
    if (!composerState.hadFocus) {
      return;
    }
    textarea.focus();
    if (
      typeof composerState.selectionStart === "number" &&
      typeof composerState.selectionEnd === "number"
    ) {
      const start = Math.min(Math.max(composerState.selectionStart, 0), textarea.value.length);
      const end = Math.min(Math.max(composerState.selectionEnd, start), textarea.value.length);
      textarea.setSelectionRange(start, end);
    }
  }

  function renderChatCallStrip(conversation) {
    const call = peers.voice.activeAgentVoiceCall;
    if (!call || peers.voice.agentVoiceCallIsTerminal(call) || call.agentId !== conversation.agent_id) {
      return "";
    }
    return `
      <section class="chat-call-strip">
        <span class="state-pill ${escapeAttr(peers.voice.agentVoiceCallPillClass(call))}">${escapeHtml(peers.voice.agentVoiceCallStatusLabel(call))}</span>
        <button type="button" data-agent-call-mute>${call.muted ? "Unmute" : "Mute"}</button>
        <button type="button" data-agent-call-end>End</button>
      </section>
    `;
  }

  function renderChatMessage(message) {
    const sender = text(message.sender) || "system";
    const metadata = message.metadata || {};
    const status = text(message.status);
    const type = text(message.message_type) || "text";
    const attachments = Array.isArray(message.attachments) ? message.attachments : [];
    const externalDelivery = metadata.external_delivery && typeof metadata.external_delivery === "object"
      ? metadata.external_delivery
      : null;
    const attachmentImport = metadata.external_attachment_import && typeof metadata.external_attachment_import === "object"
      ? metadata.external_attachment_import
      : null;
    const mutationMode = sender === "operator" ? text(message.mutation_mode) : "";
    const mutationModeLabel = mutationMode === "project_scoped"
      ? "Mutation enabled"
      : mutationMode === "reply_only"
        ? "Reply only"
        : "";
    return `
      <article class="chat-message chat-message--${escapeAttr(sender)} chat-message--${escapeAttr(type)}">
        <div class="chat-message-bubble">
          ${message.body ? `<p>${renderIdentifierText(message.body)}</p>` : ""}
          ${attachments.length ? `<div class="chat-message-attachments">${attachments.map(renderChatAttachment).join("")}</div>` : ""}
          ${attachmentImportWarning(attachmentImport)}
          <div class="chat-message-meta">
            <span>${escapeHtml(chatMessageSenderLabel(sender, metadata))}</span>
            <time>${escapeHtml(formatChatTimestamp(message.created_at))}</time>
            ${status ? `<span>${escapeHtml(chatStatusLabel(status))}</span>` : ""}
            ${mutationModeLabel ? `<span class="chat-mutation-mode ${mutationMode === "project_scoped" ? "is-enabled" : "is-reply-only"}">${escapeHtml(mutationModeLabel)}</span>` : ""}
            ${externalDelivery ? `<span title="${escapeAttr(externalChatDeliveryTitle(externalDelivery))}">${escapeHtml(externalChatDeliveryLabel(externalDelivery))}</span>` : ""}
          </div>
          ${message.error ? `<div class="chat-message-error">${renderIdentifierText(message.error)}</div>` : ""}
        </div>
      </article>
    `;
  }

  function attachmentImportWarning(summary) {
    const status = text(summary?.status);
    if (!["partial", "failed"].includes(status)) return "";
    const imported = Number(summary?.imported_count || 0);
    const failed = Number(summary?.failed_count || 0);
    const label = status === "partial"
      ? `${imported} external attachment${imported === 1 ? "" : "s"} imported; ${failed} could not be imported.`
      : `External attachments could not be imported (${failed || "unknown"} failure${failed === 1 ? "" : "s"}).`;
    const details = Array.isArray(summary?.failures)
      ? summary.failures.map((item) => `${text(item?.filename) || text(item?.attachment_id) || "attachment"}: ${text(item?.message) || text(item?.code) || "failed"}`).join(" · ")
      : "";
    return `<div class="chat-message-error"${details ? ` title="${escapeAttr(details)}"` : ""}>${escapeHtml(label)}</div>`;
  }

  function externalChatDeliveryLabel(delivery) {
    const state = text(delivery?.state) || "pending";
    const provider = text(delivery?.provider);
    return `${provider ? `${provider} ` : "External "}${state}`;
  }

  function externalChatDeliveryTitle(delivery) {
    const parts = [externalChatDeliveryLabel(delivery)];
    const attempts = Number(delivery?.attempt_count || 0);
    if (attempts > 0) parts.push(`attempt ${attempts}`);
    if (text(delivery?.last_error)) parts.push(text(delivery.last_error));
    return parts.join(" · ");
  }

  function renderChatAttachment(attachment) {
    const url = text(attachment.download_url);
    const mimeType = text(attachment.mime_type);
    const filename = text(attachment.filename) || "attachment";
    const image = ["image/png", "image/jpeg", "image/gif", "image/webp"].includes(mimeType);
    const linkAttributes = image
      ? ' target="_blank" rel="noopener"'
      : ` download="${escapeAttr(filename)}"`;
    return `
      <a class="chat-message-attachment${image ? " is-image" : ""}" href="${escapeAttr(url)}"${linkAttributes}>
        ${image ? `<img src="${escapeAttr(url)}" alt="${escapeAttr(filename)}" loading="lazy" />` : `<span class="chat-file-icon" aria-hidden="true">↧</span>`}
        <span><strong>${escapeHtml(filename)}</strong><small>${escapeHtml(formatFileSize(attachment.byte_count))}${mimeType ? ` · ${escapeHtml(mimeType)}` : ""}</small></span>
      </a>
    `;
  }

  function formatFileSize(value) {
    const bytes = Number(value || 0);
    if (!Number.isFinite(bytes) || bytes < 1024) return `${Math.max(0, bytes)} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
  }

  function chatMessageListSignature(conversation, messages, olderLoading) {
    const messageSignature = messages
      .map((message) =>
        [
          message.id,
          message.sender,
          message.message_type,
          message.body,
          message.status,
          message.error,
          JSON.stringify(message.metadata || {}),
          JSON.stringify(message.attachments || []),
          message.created_at,
          message.updated_at,
        ].map(text).join("\x1f"),
      )
      .join("\x1e");
    return [
      conversation.has_older ? "older" : "no-older",
      olderLoading ? "loading" : "idle",
      messageSignature,
    ].join("\x1d");
  }

  function setChatMessageListSignature(conversationId, signature) {
    const list = chatMessageListElement(conversationId);
    if (list) {
      list.dataset.chatMessageSignature = signature;
    }
  }

  function applyChatMessagePage(conversationId, payload) {
    const conversation = chatConversationById(conversationId);
    if (!conversation) {
      return;
    }
    replaceChatConversation({
      ...conversation,
      messages: Array.isArray(payload.messages) ? payload.messages : [],
      message_count: payload.message_count ?? conversation.message_count,
      has_older: Boolean(payload.has_older),
      oldest_message_id: payload.oldest_message_id || null,
      newest_message_id: payload.newest_message_id || null,
    });
  }

  function mergeChatConversation(existing, incoming) {
    if (!existing) {
      return normalizeChatConversation(incoming);
    }
    const incomingMessages = conversationMessages(incoming);
    const existingMessages = conversationMessages(existing);
    const mergedMessages = incomingMessages.length
      ? mergeChatMessages(existingMessages, incomingMessages)
      : existingMessages;
    const next = {
      ...existing,
      ...incoming,
      messages: mergedMessages,
    };
    if (existingMessages.length && incomingMessages.length) {
      const existingOldest = existingMessages[0];
      const incomingOldest = incomingMessages[0];
      if (compareChatMessages(existingOldest, incomingOldest) < 0) {
        next.has_older = existing.has_older;
      }
    }
    const normalized = normalizeChatConversation(next);
    if (!incomingMessages.length) {
      const incomingNewestMessageId = text(incoming?.newest_message_id);
      const incomingNewestMessageSender = text(incoming?.newest_message_sender);
      if (incomingNewestMessageId) {
        normalized.newest_message_id = incomingNewestMessageId;
      }
      if (incomingNewestMessageSender) {
        normalized.newest_message_sender = incomingNewestMessageSender;
      }
    }
    return normalized;
  }

  function normalizeChatConversation(conversation) {
    const messagesLoaded = Array.isArray(conversation?.messages) || conversation?.messages_loaded === true;
    const messages = conversationMessages(conversation);
    const newestMessage = messages[messages.length - 1] || null;
    return {
      ...conversation,
      messages,
      messages_loaded: messagesLoaded,
      has_older: Boolean(conversation?.has_older),
      oldest_message_id: text(messages[0]?.id) || text(conversation?.oldest_message_id) || null,
      newest_message_id: text(newestMessage?.id) || text(conversation?.newest_message_id) || null,
      newest_message_sender: text(newestMessage?.sender) || text(conversation?.newest_message_sender) || null,
    };
  }

  function conversationMessages(conversation) {
    return Array.isArray(conversation?.messages)
      ? [...conversation.messages].sort(compareChatMessages)
      : [];
  }

  function mergeChatMessages(existingMessages, incomingMessages) {
    const byId = new Map();
    [...existingMessages, ...incomingMessages].forEach((message) => {
      if (message?.id) {
        byId.set(text(message.id), {
          ...(byId.get(text(message.id)) || {}),
          ...message,
        });
      }
    });
    return Array.from(byId.values()).sort(compareChatMessages);
  }

  function compareChatMessages(left, right) {
    const leftTime = Date.parse(left?.created_at || "");
    const rightTime = Date.parse(right?.created_at || "");
    if (Number.isFinite(leftTime) && Number.isFinite(rightTime) && leftTime !== rightTime) {
      return leftTime - rightTime;
    }
    const leftCreated = text(left?.created_at);
    const rightCreated = text(right?.created_at);
    if (leftCreated !== rightCreated) {
      return leftCreated.localeCompare(rightCreated);
    }
    return text(left?.id).localeCompare(text(right?.id));
  }

  function renderChatAvatar(agent) {
    const label = text(agent?.display_name || agent?.id || "?");
    const avatarAsset = text(agent?.avatar?.asset_id);
    if (avatarAsset) {
      return `<img class="chat-avatar" src="${API_BASE}/avatars/${encodeURIComponent(avatarAsset)}" alt="" />`;
    }
    return `<span class="chat-avatar chat-avatar-fallback" aria-hidden="true">${escapeHtml(label.slice(0, 1).toUpperCase() || "?")}</span>`;
  }

  function selectedChatConversation() {
    return chatConversationById(selectedChatConversationId);
  }

  function chatConversationById(conversationId) {
    const normalizedConversationId = text(conversationId);
    if (!normalizedConversationId) {
      return null;
    }
    return chatState.conversations.find((conversation) => text(conversation.id) === normalizedConversationId) || null;
  }

  function conversationForAgent(agentId) {
    const normalizedAgentId = text(agentId);
    return chatState.conversations.find((conversation) => text(conversation.agent_id) === normalizedAgentId) || null;
  }

  function chatMessageListElement(conversationId = selectedChatConversationId) {
    const normalizedConversationId = text(conversationId);
    if (!normalizedConversationId || !elements.chatThreadView) {
      return null;
    }
    return Array.from(elements.chatThreadView.querySelectorAll(".chat-message-list")).find((list) => {
      return text(list.dataset.chatScrollConversationId) === normalizedConversationId;
    }) || null;
  }

  function captureChatScroll(conversationId = selectedChatConversationId) {
    const list = chatMessageListElement(conversationId);
    if (!list) {
      return null;
    }
    return {
      scrollTop: list.scrollTop,
      scrollHeight: list.scrollHeight,
    };
  }

  function chatIsScrolledNearBottom(conversationId = selectedChatConversationId) {
    const list = chatMessageListElement(conversationId);
    if (!list) {
      return true;
    }
    return list.scrollHeight - list.clientHeight - list.scrollTop <= chatBottomStickinessPx;
  }

  function scheduleChatScrollToBottom(conversationId) {
    pendingChatScroll = { conversationId: text(conversationId), mode: "bottom" };
  }

  function scheduleChatScrollForRender(conversationId = selectedChatConversationId, { preferBottom = false } = {}) {
    const normalizedConversationId = text(conversationId);
    if (!normalizedConversationId) {
      return;
    }
    if (
      pendingChatScroll?.mode === "bottom" &&
      text(pendingChatScroll.conversationId) === normalizedConversationId
    ) {
      return;
    }
    const snapshot = captureChatScroll(normalizedConversationId);
    if (!snapshot) {
      return;
    }
    if (preferBottom || chatIsScrolledNearBottom(normalizedConversationId)) {
      scheduleChatScrollToBottom(normalizedConversationId);
      return;
    }
    scheduleChatScrollPreserve(normalizedConversationId, snapshot);
  }

  function scheduleChatScrollPreserve(conversationId, snapshot) {
    pendingChatScroll = {
      conversationId: text(conversationId),
      mode: "preserve",
      scrollTop: snapshot.scrollTop,
    };
  }

  function scheduleChatScrollPreservePrepend(conversationId, snapshot) {
    pendingChatScroll = {
      conversationId: text(conversationId),
      mode: "preserve-prepend",
      scrollTop: snapshot.scrollTop,
      scrollHeight: snapshot.scrollHeight,
    };
  }

  function bindChatMessageListScroll(conversationId) {
    const list = chatMessageListElement(conversationId);
    if (!list) {
      return;
    }
    list.addEventListener("scroll", () => {
      if (list.scrollTop <= chatTopLoadThresholdPx) {
        void loadOlderChatMessages(conversationId);
      }
    });
  }

  function applyPendingChatScroll(conversationId) {
    const pending = pendingChatScroll;
    if (!pending || text(pending.conversationId) !== text(conversationId)) {
      return;
    }
    const applyScroll = () => {
      const list = chatMessageListElement(conversationId);
      if (!list || pendingChatScroll !== pending) {
        return false;
      }
      if (pending.mode === "bottom") {
        list.scrollTop = list.scrollHeight;
      } else if (pending.mode === "preserve-prepend") {
        list.scrollTop = Math.max(0, list.scrollHeight - pending.scrollHeight + pending.scrollTop);
      } else {
        list.scrollTop = pending.scrollTop;
      }
      return true;
    };
    window.requestAnimationFrame(() => {
      if (!applyScroll()) {
        return;
      }
      window.requestAnimationFrame(() => {
        applyScroll();
        if (pendingChatScroll === pending) {
          pendingChatScroll = null;
        }
      });
    });
  }

  function compareChatConversations(left, right) {
    return (
      text(right.last_interaction_at).localeCompare(text(left.last_interaction_at)) ||
      byId(left, right)
    );
  }

  function chatMessageSenderLabel(sender, metadata = {}) {
    if (sender === "operator") {
      const label = text(metadata.operator_display_name) || text(metadata.operator_user_id);
      return label ? `Human operator: ${label}` : "Human operator";
    }
    if (sender === "agent") {
      return "Agent";
    }
    return "System";
  }

  function chatStatusLabel(status) {
    const labels = {
      created: "Created",
      dispatching: "Dispatching",
      queued: "Queued",
      running: "Sent",
      delivered_in_dispatch: "Delivered",
      acknowledged: "Handled",
      replied: "Replied",
      failed: "Failed",
      sent: "Sent",
      ended: "Ended",
    };
    return labels[status] || humanizeToken(status);
  }

  function formatChatTimestamp(value) {
    const raw = text(value);
    if (!raw) {
      return "";
    }
    const date = new Date(raw);
    if (Number.isNaN(date.getTime())) {
      return raw;
    }
    return date.toLocaleString([], {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }
  function bindMessageSubmission() {

    document.addEventListener("submit", (event) => {
      const form = event.target?.closest?.("[data-chat-message-form]");
      if (!form) {
        return;
      }
      event.preventDefault();
      void submitChatMessage(form);
    });
  }
  function bindComposerKeyboard() {

    document.addEventListener("keydown", (event) => {
      const chatComposer = event.target?.closest?.(
        '[data-chat-message-form] textarea[name="body"]',
      );
      if (
        !(chatComposer instanceof HTMLTextAreaElement) ||
        event.key !== "Enter" ||
        event.shiftKey ||
        event.isComposing
      ) {
        return;
      }
      const form = chatComposer.closest("form[data-chat-message-form]");
      if (!(form instanceof HTMLFormElement)) {
        return;
      }
      event.preventDefault();
      form.requestSubmit();
    });
  }
  function bindAttachmentPolicySubmission() {

    elements.chatAttachmentPolicyForm?.addEventListener("submit", (event) => {
      event.preventDefault();
      void submitChatAttachmentPolicy(elements.chatAttachmentPolicyForm);
    });
  }
  function bindAttachmentPolicyControls() {

    document.addEventListener("click", (event) => {
      const openButton = event.target?.closest?.("[data-chat-attachment-policy-open]");
      if (openButton) {
        event.preventDefault();
        void openChatAttachmentPolicy();
        return;
      }
      if (event.target?.closest?.("[data-chat-attachment-policy-close]")) {
        event.preventDefault();
        closeChatAttachmentPolicy();
      }
    });
  }
  function handleChatSettingsChange(event) {
    const soundToggle = event.target?.closest?.("[data-chat-notification-sound]");
    if (soundToggle instanceof HTMLInputElement) {
      chatNotificationSoundMuted = !soundToggle.checked;
      storeChatNotificationSoundMuted();
      return true;
    }

    const attachmentInput = event.target?.closest?.("[data-chat-attachment-input]");
    if (attachmentInput instanceof HTMLInputElement) {
      const conversationId = text(attachmentInput.dataset.chatAttachmentInput);
      pendingChatAttachments.set(conversationId, [...(attachmentInput.files || [])]);
      renderChat();
      return true;
    }

    const select = event.target?.closest?.("[data-chat-operator-select]");
    if (!select) {
      return true;
    }
    rememberCurrentChatComposerDraft();
    selectedChatOperatorUserId = text(select.value) || null;
    selectedChatConversationId = null;
    chatNewPickerOpen = false;
    chatMobileConversationListOpen = true;
    closeChatStream();
    void loadConversations({ preserveSelection: false });
    return false;
  }
  function handleComposerInput(event) {
    const chatComposer = event.target?.closest?.(
      '[data-chat-message-form] textarea[name="body"]',
    );
    if (chatComposer instanceof HTMLTextAreaElement) {
      rememberCurrentChatComposerDraft();
      return true;
    }
    return false;
  }
  function bindAttachmentRemoval() {

    document.addEventListener("click", (event) => {
      const removeAttachment = event.target?.closest?.("[data-chat-attachment-remove]");
      if (!removeAttachment) {
        return;
      }
      event.preventDefault();
      const conversationId = text(removeAttachment.dataset.chatAttachmentConversationId);
      const index = Number(removeAttachment.dataset.chatAttachmentRemove);
      const files = [...(pendingChatAttachments.get(conversationId) || [])];
      if (Number.isInteger(index)) {
        files.splice(index, 1);
      }
      pendingChatAttachments.set(conversationId, files);
      renderChat();
    });
  }
  function bindConversationDraftCapture() {

    document.addEventListener("pointerdown", (event) => {
      if (
        event.target?.closest?.(
          ".chat-conversation-row[data-chat-conversation-id]",
        )
      ) {
        rememberCurrentChatComposerDraft();
      }
    });
  }
  function handleOpenAgentChatClick(event) {
    const openChatButton = event.target.closest("[data-open-chat-agent-id]");
    if (openChatButton) {
      event.preventDefault();
      void openChatForAgent(openChatButton.dataset.openChatAgentId);
      return true;
    }
    return false;
  }
  function handleChatNavigationClick(event) {
    const chatNewButton = event.target.closest("[data-chat-new]");
    if (chatNewButton) {
      event.preventDefault();
      if (chatState.read_only) {
        peers.app.setStatus("This chat history is read-only.", "error");
        return true;
      }
      chatNewPickerOpen = !chatNewPickerOpen;
      renderChat();
      return true;
    }

    const chatMobileConversationsButton = event.target.closest("[data-chat-mobile-conversations]");
    if (chatMobileConversationsButton) {
      event.preventDefault();
      rememberCurrentChatComposerDraft();
      chatMobileConversationListOpen = true;
      renderChat();
      return true;
    }

    const chatAgentButton = event.target.closest("[data-chat-agent-id]");
    if (chatAgentButton) {
      event.preventDefault();
      void createOrFocusChat(chatAgentButton.dataset.chatAgentId);
      return true;
    }

    const chatConversationButton = event.target.closest(".chat-conversation-row[data-chat-conversation-id]");
    if (chatConversationButton) {
      event.preventDefault();
      rememberCurrentChatComposerDraft();
      selectedChatConversationId = chatConversationButton.dataset.chatConversationId || null;
      chatMobileConversationListOpen = false;
      markChatConversationRead(selectedChatConversationId, { rerender: false });
      renderChat();
      if (selectedChatConversationId) {
        ensureChatStream(selectedChatConversationId);
        void loadConversationDetail(selectedChatConversationId);
      }
      return true;
    }

    const chatRefreshButton = event.target.closest("[data-chat-refresh]");
    if (chatRefreshButton) {
      event.preventDefault();
      void loadConversations({ preserveSelection: true });
      return true;
    }
    return false;
  }

  function applyBootstrapScope(payload) {
    selectedChatOperatorUserId = text(payload.selected_operator?.user_id) || selectedChatOperatorUserId;
    chatState = normalizeChat({ ...chatState, ...chatScopeFields(payload), revision: payload.revision ?? chatState.revision });
  }

  function selectVoiceConversation(conversationId) {
    selectedChatConversationId = conversationId;
  }

  return {
    applyBootstrapScope,
    selectVoiceConversation,
    bindAttachmentPolicyControls,
    bindAttachmentRemoval,
    bindComposerKeyboard,
    bindConversationDraftCapture,
    bindMessageSubmission,
    bindAttachmentPolicySubmission,
    get chatState() { return chatState; },
    closeChatStream,
    formatChatTimestamp,
    handleChatSettingsChange,
    handleComposerInput,
    handleOpenAgentChatClick,
    handleChatNavigationClick,
    loadChatView,
    loadConversations,
    markChatConversationRead,
    openChatForAgent,
    renderChat,
    scheduleChatScrollToBottom,
    get selectedChatConversationId() { return selectedChatConversationId; },
    syncChatStreamForActiveView,
    syncTeamStateFromChatPayload,
    unreadChatConversationCount,
  };
}
