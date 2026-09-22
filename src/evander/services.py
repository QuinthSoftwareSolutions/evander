# ruff: noqa: F401
"""Compatibility imports for integration services.

Implementations live in :mod:`evander.integration_services` by provider domain.
"""

from __future__ import annotations
import base64
from contextlib import contextmanager
import fcntl
import html
import json
import logging
import mimetypes
import os
import random
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Iterator, Literal, Mapping, Protocol
from urllib.parse import quote, unquote, urlparse, urlunparse
import httpx
from evander.ado_mentions import (
    AdoResolvedMention,
    AdoMentionResolver,
    normalize_ado_mention_refs,
    render_ado_mentions_prefix,
    resolve_ado_mentions,
)
from evander.models import (
    AdoIdentitySummary,
    AdoPrCommentActivityResponse,
    AdoPrCommitActivityResponse,
    DiscordCommandEnvelope,
    DiscordMessageEnvelope,
    HasPendingDiscordMessagesResponse,
    HasPendingFeedbackResponse,
    GetPullRequestResponse,
    InlineMediaRef,
    ListPullRequestsResponse,
    ListUnreadDiscordMessagesResponse,
    ListUnreadFeedbackResponse,
    PostPrFeedbackReplyResponse,
    PullRequestCommit,
    PullRequestItem,
    PullRequestReviewer,
    PullRequestWorkItem,
    PullRequestWorkItemsResponse,
    PrFeedbackItem,
    ResolveAdoIdentityResponse,
    SessionManifest,
    TeamsMessageEnvelope,
    UpdatePullRequestResponse,
)
from evander.issue_owner import assigned_to_identity
from evander.messaging.discord_inbox_store import DiscordInboxStore
from evander.discord_webhooks import (
    DiscordWebhookAvatar,
    DiscordWebhookCredential,
    DiscordWebhookCredentialStore,
    discord_webhook_credential_key,
    prepare_discord_webhook_avatar,
)
from evander.outbound_identity import OutboundIdentity
from evander.repositories.cursors import DiscordInboxCursorRepository
from evander.inline_media import (
    InlineMediaDownload,
    inline_media_download_from_embedded_payload,
    sanitize_inline_media_metadata,
)
from evander.email.providers import (
    EmailAttachmentContent,
    EmailAttachmentMetadata,
    EmailDraft,
    EmailDraftAttachment,
    EmailMessageDetail,
    EmailMessagePage,
    EmailMessageSearchQuery,
    EmailMessageSummary,
    EmailProvider,
    EmailSendResult,
)
from evander.repositories.records import DiscordRecordsRepository, TeamsRecordsRepository
from evander.text_formatting import render_markdownish_text_to_html
from evander.pr_feedback.providers import (
    _ado_line_from_context,
    _ado_should_skip_comment,
    _ado_thread_resolved,
    pr_feedback_item_payload,
)
from evander.pr_feedback.identity import pr_feedback_stream_aliases
from evander.pr_feedback.store import PrFeedbackRepository, PrFeedbackStore, _stream_key

from evander.integration_services.common import (
    _ATTACHMENT_SECRET_KEYS as _ATTACHMENT_SECRET_KEYS,
    _IGNORED_FEEDBACK_RETENTION as _IGNORED_FEEDBACK_RETENTION,
    _LOGGER as _LOGGER,
    _ado_identity_summary as _ado_identity_summary,
    _artifact_bytes as _artifact_bytes,
    _artifact_content_type as _artifact_content_type,
    _artifact_filename as _artifact_filename,
    _buffer_streaming_response as _buffer_streaming_response,
    _datetime_in_range as _datetime_in_range,
    _identity_email as _identity_email,
    _identity_match_values as _identity_match_values,
    _identity_matches as _identity_matches,
    _identity_property as _identity_property,
    _iso_z as _iso_z,
    _message_sort_value as _message_sort_value,
    _normalize_optional_text as _normalize_optional_text,
    _normalize_string_list as _normalize_string_list,
    _parse_dt as _parse_dt,
    _parse_optional_dt as _parse_optional_dt,
    _parse_snowflake as _parse_snowflake,
    _pull_request_work_item_assignee as _pull_request_work_item_assignee,
    _pull_request_work_item_from_reference as _pull_request_work_item_from_reference,
    _sanitize_attachment_metadata as _sanitize_attachment_metadata,
    _sanitize_message_payload_attachments as _sanitize_message_payload_attachments,
    _snippet as _snippet,
    _text_or_none as _text_or_none,
    _unique_positive_ints as _unique_positive_ints,
    normalize_pr_automation_provider as normalize_pr_automation_provider,
    normalize_pr_feedback_provider as normalize_pr_feedback_provider,
)

from evander.integration_services.discord import (
    DiscordBotApiService as DiscordBotApiService,
    DiscordInboxService as DiscordInboxService,
    DiscordService as DiscordService,
    StubDiscordService as StubDiscordService,
    _put_discord_commands as _put_discord_commands,
)

from evander.integration_services.email import (
    EmailService as EmailService,
)

from evander.integration_services.pull_requests import (
    AzureDevOpsPrPublishProvider as AzureDevOpsPrPublishProvider,
    AzureDevOpsPrQueryProvider as AzureDevOpsPrQueryProvider,
    AzureDevOpsPrReplyProvider as AzureDevOpsPrReplyProvider,
    GitHubPrPublishProvider as GitHubPrPublishProvider,
    GitHubPrQueryProvider as GitHubPrQueryProvider,
    GitHubPrReplyProvider as GitHubPrReplyProvider,
    PrFeedbackService as PrFeedbackService,
    PrPublishProvider as PrPublishProvider,
    PrPublishService as PrPublishService,
    PrQueryProvider as PrQueryProvider,
    PrQueryService as PrQueryService,
    PrReplyProvider as PrReplyProvider,
    PrReplyService as PrReplyService,
    _AzureDevOpsPrChange as _AzureDevOpsPrChange,
    _AzureDevOpsPrIterationContext as _AzureDevOpsPrIterationContext,
    _PullRequestTagExpression as _PullRequestTagExpression,
    _PullRequestTagExpressionParser as _PullRequestTagExpressionParser,
    _PullRequestTagToken as _PullRequestTagToken,
    _activity_max_candidates as _activity_max_candidates,
    _activity_statuses as _activity_statuses,
    _ado_resolved_mention_payloads as _ado_resolved_mention_payloads,
    _append_markdown_attachment_links as _append_markdown_attachment_links,
    _as_int_or_none as _as_int_or_none,
    _azure_devops_comment_file_path as _azure_devops_comment_file_path,
    _azure_devops_error_details as _azure_devops_error_details,
    _azure_devops_inline_comment_error_context as _azure_devops_inline_comment_error_context,
    _azure_devops_pr_change_from_payload as _azure_devops_pr_change_from_payload,
    _azure_devops_pr_iteration_context as _azure_devops_pr_iteration_context,
    _azure_devops_pull_request_thread_context_for_review_comment as _azure_devops_pull_request_thread_context_for_review_comment,
    _azure_devops_thread_context_for_review_comment as _azure_devops_thread_context_for_review_comment,
    _azure_pr_web_url as _azure_pr_web_url,
    _evaluate_pull_request_tag_expression as _evaluate_pull_request_tag_expression,
    _exclusive_json_file_lock as _exclusive_json_file_lock,
    _expand_pull_request_statuses as _expand_pull_request_statuses,
    _feedback_id_for_reply_response as _feedback_id_for_reply_response,
    _normalize_azure_devops_pr_path as _normalize_azure_devops_pr_path,
    _normalize_azure_devops_repo_identifier as _normalize_azure_devops_repo_identifier,
    _normalize_github_feedback_kind as _normalize_github_feedback_kind,
    _normalize_ignored_feedback_payload as _normalize_ignored_feedback_payload,
    _normalize_pr_review_comments as _normalize_pr_review_comments,
    _normalize_pr_review_event as _normalize_pr_review_event,
    _parse_pull_request_tag_expression as _parse_pull_request_tag_expression,
    _pr_artifact_bytes as _pr_artifact_bytes,
    _pr_artifact_filename as _pr_artifact_filename,
    _pr_artifact_is_image as _pr_artifact_is_image,
    _pr_artifact_mime_type as _pr_artifact_mime_type,
    _publication_feedback_ids as _publication_feedback_ids,
    _pull_request_tags_match as _pull_request_tags_match,
    _raise_azure_devops_pr_publish_error as _raise_azure_devops_pr_publish_error,
    _read_quoted_pull_request_tag as _read_quoted_pull_request_tag,
    _reject_unsupported_attachments as _reject_unsupported_attachments,
    _reject_unsupported_comment_attachments as _reject_unsupported_comment_attachments,
    _reject_unsupported_mentions as _reject_unsupported_mentions,
    _tokenize_pull_request_tag_expression as _tokenize_pull_request_tag_expression,
    _upload_azure_devops_pr_attachments as _upload_azure_devops_pr_attachments,
    _write_json_atomic as _write_json_atomic,
    ignored_feedback_ids_for_scope as ignored_feedback_ids_for_scope,
    pr_feedback_scope_key as pr_feedback_scope_key,
    pr_reply_state_namespace as pr_reply_state_namespace,
    read_ignored_feedback_payload as read_ignored_feedback_payload,
)

from evander.integration_services.teams import (
    GraphTeamsService as GraphTeamsService,
    StubTeamsService as StubTeamsService,
    TeamsAuthenticationError as TeamsAuthenticationError,
    TeamsInlineMediaProvider as TeamsInlineMediaProvider,
    TeamsService as TeamsService,
    TeamsTarget as TeamsTarget,
    _GRAPH_TEAMS_REACTION_TYPE_BY_VALUE as _GRAPH_TEAMS_REACTION_TYPE_BY_VALUE,
    _GRAPH_TEAMS_SUPPORTED_REACTION_ALIASES as _GRAPH_TEAMS_SUPPORTED_REACTION_ALIASES,
    _TEAMS_TARGET_ERROR as _TEAMS_TARGET_ERROR,
    _TeamsGraphPaginationBudget as _TeamsGraphPaginationBudget,
    _TeamsMentionTarget as _TeamsMentionTarget,
    _artifact_control_plane_download_url as _artifact_control_plane_download_url,
    _artifact_is_inline_teams_image as _artifact_is_inline_teams_image,
    _artifact_teams_reference_content_url as _artifact_teams_reference_content_url,
    _augment_http_status_error as _augment_http_status_error,
    _filter_teams_messages_since_cursor as _filter_teams_messages_since_cursor,
    _graph_error_details as _graph_error_details,
    _normalize_graph_teams_reaction_type as _normalize_graph_teams_reaction_type,
    _normalize_teams_allowlist_targets as _normalize_teams_allowlist_targets,
    _normalize_teams_or_target_refs as _normalize_teams_or_target_refs,
    _parse_graph_datetime as _parse_graph_datetime,
    _parse_teams_cursor as _parse_teams_cursor,
    _put_teams_messages as _put_teams_messages,
    _quote_teams_reference_content_url as _quote_teams_reference_content_url,
    _sorted_teams_messages as _sorted_teams_messages,
    _teams_attachment_id as _teams_attachment_id,
    _teams_content_type_extension as _teams_content_type_extension,
    _teams_hosted_content_attachment as _teams_hosted_content_attachment,
    _teams_hosted_content_filename as _teams_hosted_content_filename,
    _teams_hosted_content_id_from_url as _teams_hosted_content_id_from_url,
    _teams_message_to_html as _teams_message_to_html,
    _teams_payload_attachments as _teams_payload_attachments,
    effective_teams_channel_targets as effective_teams_channel_targets,
    normalize_teams_channel_or_target_refs as normalize_teams_channel_or_target_refs,
    normalize_teams_channel_targets as normalize_teams_channel_targets,
    normalize_teams_chat_or_target_refs as normalize_teams_chat_or_target_refs,
    normalize_teams_chat_targets as normalize_teams_chat_targets,
    normalize_teams_mention_refs as normalize_teams_mention_refs,
    normalize_teams_target as normalize_teams_target,
    normalize_teams_user_or_target_refs as normalize_teams_user_or_target_refs,
    normalize_teams_user_target as normalize_teams_user_target,
    parse_teams_target as parse_teams_target,
    resolve_teams_send_target as resolve_teams_send_target,
    teams_target_from_message as teams_target_from_message,
)
