import assert from "node:assert/strict";
import test from "node:test";

import {
  workflowCommunicationTargetInspectorState,
} from "../src/workflowCommunicationTargets.ts";

function option(overrides = {}) {
  return {
    value: "target-1",
    label: "Target 1",
    ...overrides,
  };
}

function teamsThreadRules({ replySupported = false, replyShapes = [], topLevelSupported = true } = {}) {
  return {
    top_level: {
      supported: topLevelSupported,
      requires_capability: "write",
    },
    reply: {
      supported: replySupported,
      requires_capability: "reply",
      supported_target_shapes: replyShapes,
      unsupported_target_shapes: ["teams_chat", "teams_user", "discord_channel", "email_recipient"],
    },
    reply_hint: {
      supported: replySupported,
      supported_target_shapes: replyShapes,
      unsupported_target_shapes: ["teams_chat", "teams_user", "discord_channel", "email_recipient"],
    },
  };
}

test("send-message shows structured help and allows Teams channel thread replies when reply is supported", () => {
  const state = workflowCommunicationTargetInspectorState(
    "effect:communication:send-message",
    option({
      description: "Teams channel reply target",
      metadata: {
        provider: "teams",
        address: "team-a/channel-1",
        target_shape: "teams_channel",
        policy_capabilities: ["write", "reply"],
        thread_id: teamsThreadRules({ replySupported: true, replyShapes: ["teams_channel"] }),
      },
    }),
    "thread-123",
    "literal",
  );

  assert.ok(state);
  assert.equal(state.validation, null);
  assert.ok(state.targetHelp.some((line) => line.includes("Provider: Teams")));
  assert.ok(state.targetHelp.some((line) => line.includes("Target shape: Teams channel")));
  assert.ok(state.targetHelp.some((line) => line.includes("Policy capabilities: write, reply")));
  assert.match(state.threadHelp, /top-level message/);
  assert.match(state.threadHelp, /Teams channel targets can reply/);
});

test("send-message blocks Teams channel write-only targets when thread_id is literal", () => {
  const state = workflowCommunicationTargetInspectorState(
    "effect:communication:send-message",
    option({
      metadata: {
        provider: "teams",
        address: "team-a/channel-2",
        target_shape: "teams_channel",
        policy_capabilities: ["write"],
        thread_id: teamsThreadRules({ replySupported: false, replyShapes: [] }),
      },
    }),
    "thread-123",
    "literal",
  );

  assert.ok(state);
  assert.equal(state.validation?.blocked, true);
  assert.match(state.validation?.message ?? "", /thread replies/);
});

test("send-message blocks Teams chat, Teams user, and Discord literal thread replies", () => {
  const teamsChat = workflowCommunicationTargetInspectorState(
    "effect:communication:send-message",
    option({
      metadata: {
        provider: "teams",
        address: "chat:chat-1",
        target_shape: "teams_chat",
        policy_capabilities: ["write"],
        thread_id: teamsThreadRules({ replySupported: false, replyShapes: [] }),
      },
    }),
    "thread-123",
    "literal",
  );
  const discord = workflowCommunicationTargetInspectorState(
    "effect:communication:send-message",
    option({
      metadata: {
        provider: "discord",
        address: "123456789012345678/234567890123456789",
        target_shape: "discord_channel",
        policy_capabilities: ["write"],
        thread_id: teamsThreadRules({ replySupported: false, replyShapes: [] }),
      },
    }),
    "thread-123",
    "literal",
  );

  assert.equal(teamsChat?.validation?.blocked, true);
  assert.match(teamsChat?.validation?.message ?? "", /thread replies|Clear thread_id/);
  assert.equal(discord?.validation?.blocked, true);
  assert.match(discord?.validation?.message ?? "", /thread replies|Discord targets/);
});

test("send-message allows a registered thread for a thread-aware Discord target", () => {
  const state = workflowCommunicationTargetInspectorState(
    "effect:communication:send-message",
    option({
      metadata: {
        provider: "discord",
        address: "123456789012345678/234567890123456789",
        target_shape: "discord_channel",
        discord_conversation_scope: "public_threads",
        policy_capabilities: ["reply"],
        thread_id: {
          top_level: { supported: false, requires_capability: "write" },
          reply: {
            supported: true,
            requires_capability: "reply",
            supported_target_shapes: ["teams_channel", "discord_channel"],
          },
        },
      },
    }),
    "345678901234567890",
    "literal",
  );

  assert.equal(state?.validation, null);
  assert.match(state?.threadHelp ?? "", /requires a registered public thread_id/);
  assert.ok(state?.targetHelp.some((line) => line.includes("Conversation scope: public_threads")));
});

test("notification allows Teams channel thread hints and blocks email literal thread replies", () => {
  const teamsChannel = workflowCommunicationTargetInspectorState(
    "effect:notification:send",
    option({
      metadata: {
        provider: "teams",
        address: "team-a/channel-1",
        target_shape: "teams_channel",
        policy_capabilities: ["notify"],
        thread_id: teamsThreadRules({ replySupported: true, replyShapes: ["teams_channel"] }),
      },
    }),
    "thread-123",
    "literal",
  );
  const email = workflowCommunicationTargetInspectorState(
    "effect:notification:send",
    option({
      metadata: {
        provider: "email",
        address: "operator@example.com",
        target_shape: "email_recipient",
        policy_capabilities: ["notify"],
        thread_id: teamsThreadRules({ replySupported: false, replyShapes: [] }),
      },
    }),
    "thread-123",
    "literal",
  );

  assert.equal(teamsChannel?.validation, null);
  assert.match(teamsChannel?.threadHelp ?? "", /reply hint/);
  assert.equal(email?.validation?.blocked, true);
  assert.match(email?.validation?.message ?? "", /Email targets are not thread destinations|drafts rather than thread replies/);
});

test("bind and template thread values warn but do not block", () => {
  const bindState = workflowCommunicationTargetInspectorState(
    "effect:communication:send-message",
    option({
      metadata: {
        provider: "teams",
        address: "chat:chat-1",
        target_shape: "teams_chat",
        policy_capabilities: ["write"],
        thread_id: teamsThreadRules({ replySupported: false, replyShapes: [] }),
      },
    }),
    "${{upstream.thread_id}}",
    "bind",
  );
  const templateState = workflowCommunicationTargetInspectorState(
    "effect:notification:send",
    option({
      metadata: {
        provider: "discord",
        address: "123456789012345678/234567890123456789",
        target_shape: "discord_channel",
        policy_capabilities: ["notify"],
        thread_id: teamsThreadRules({ replySupported: false, replyShapes: [] }),
      },
    }),
    "reply:${{upstream.thread_id}}",
    "template",
  );

  assert.equal(bindState?.validation, null);
  assert.match(bindState?.advisory ?? "", /runtime/);
  assert.equal(templateState?.validation, null);
  assert.match(templateState?.advisory ?? "", /runtime/);
});
