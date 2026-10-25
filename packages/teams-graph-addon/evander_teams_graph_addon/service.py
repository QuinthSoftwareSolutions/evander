"""Microsoft Teams targets, media, and Graph service."""

from __future__ import annotations
import base64
import html
import random
import time
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import quote, urlparse
import httpx
from evander.models import (
    TeamsMessageEnvelope,
)
from evander.repositories.records import TeamsRecordsRepository, teams_records_with_outbound
from .message_editing import record_outbound, update_message

from evander.integration_services.common import (
    _LOGGER,
    _artifact_bytes,
    _artifact_content_type,
    _artifact_filename,
    _buffer_streaming_response,
    _sanitize_attachment_metadata,
    _sanitize_message_payload_attachments,
)


from evander.integration_services.teams import (
    ManagedTeamsService,
    TeamsAuthenticationError,
    TeamsTarget,
    _TEAMS_TARGET_ERROR,
    _TeamsGraphPaginationBudget,
    _TeamsMentionTarget,
    _artifact_control_plane_download_url,
    _artifact_is_inline_teams_image,
    _artifact_teams_reference_content_url,
    _augment_http_status_error,
    _filter_teams_messages_since_cursor,
    _normalize_graph_teams_reaction_type,
    _parse_graph_datetime,
    _parse_teams_cursor,
    _put_teams_messages,
    _sorted_teams_messages,
    _teams_attachment_id,
    _teams_content_type_extension,
    _teams_hosted_content_attachment,
    _teams_hosted_content_id_from_url,
    _teams_message_to_html,
    _teams_payload_attachments,
    normalize_teams_mention_refs,
    normalize_teams_target,
    normalize_teams_user_target,
    parse_teams_target,
    teams_target_from_message,
)


class GraphTeamsService(ManagedTeamsService):
    def __init__(
        self,
        message_store: TeamsRecordsRepository,
        *,
        token: str | None = None,
        token_provider: Callable[[], str] | None = None,
        tenant_id_provider: Callable[[], str | None] | None = None,
        api_base_url: str,
        poll_limit: int,
        timeout_seconds: float,
        history_page_cap: int = 1,
        history_deadline_seconds: float = 15.0,
        history_request_timeout_seconds: float = 5.0,
        retry_count: int = 2,
        retry_base_backoff_seconds: float = 0.5,
        retry_max_backoff_seconds: float = 5.0,
        allowed_channel_targets_provider: Callable[[], list[str]] = lambda: [],
        allowed_user_targets_provider: Callable[[], list[str]] = lambda: [],
        user_chat_cache: dict[str, str] | None = None,
        user_chat_cache_namespace: str = "default",
        read_reaction_emoji: str = "👀",
        defer_retry_after: bool = False,
        defer_timeout_retry: bool = False,
    ) -> None:
        self.message_store = teams_records_with_outbound(message_store)
        self.token = str(token or "").strip()
        self.token_provider = token_provider or (lambda: self.token)
        self.tenant_id_provider = tenant_id_provider
        self.api_base_url = api_base_url.rstrip("/")
        self.poll_limit = poll_limit
        self.history_page_cap = max(1, int(history_page_cap))
        self.history_deadline_seconds = max(0.001, float(history_deadline_seconds))
        self.history_request_timeout_seconds = max(0.001, float(history_request_timeout_seconds))
        self.timeout_seconds = timeout_seconds
        self.retry_count = retry_count
        self.retry_base_backoff_seconds = retry_base_backoff_seconds
        self.retry_max_backoff_seconds = retry_max_backoff_seconds
        self.max_total_retry_backoff_seconds = sum(
            min(self.retry_max_backoff_seconds, self.retry_base_backoff_seconds * (2**attempt))
            for attempt in range(self.retry_count)
        )
        self.allowed_channel_targets_provider = allowed_channel_targets_provider
        self.allowed_user_targets_provider = allowed_user_targets_provider
        self.user_chat_cache = user_chat_cache if user_chat_cache is not None else {}
        self.user_chat_cache_namespace = str(user_chat_cache_namespace or "default")
        self.read_reaction_emoji = read_reaction_emoji
        self.defer_retry_after = defer_retry_after
        self.defer_timeout_retry = defer_timeout_retry

    def _resolve_token(self) -> str:
        try:
            token = str(self.token_provider() or "").strip()
        except TeamsAuthenticationError:
            raise
        except Exception as exc:
            raise TeamsAuthenticationError(str(exc)) from exc
        if not token:
            raise TeamsAuthenticationError(
                "Teams Graph access token provider returned an empty token."
            )
        self.token = token
        return token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._resolve_token()}",
            "Content-Type": "application/json",
        }

    def _resolve_tenant_id(self) -> str | None:
        if self.tenant_id_provider is None:
            return None
        tenant_id = str(self.tenant_id_provider() or "").strip()
        return tenant_id or None

    def _allowed_channel_targets(self) -> list[str]:
        return sorted(
            {
                normalize_teams_target(target)
                for target in self.allowed_channel_targets_provider()
                if target.strip()
            }
        )

    def _allowed_user_targets(self) -> list[str]:
        return sorted(
            {
                normalize_teams_user_target(target)
                for target in self.allowed_user_targets_provider()
                if str(target or "").strip()
            }
        )

    def _user_chat_cache_key(self, user_target: str) -> str:
        normalized_user_target = normalize_teams_user_target(user_target)
        return f"{self.user_chat_cache_namespace}:{normalized_user_target}"

    def _user_id_cache_key(self, user_ref: str) -> str:
        normalized_user_target = normalize_teams_user_target(user_ref)
        return f"{self.user_chat_cache_namespace}:user-id:{normalized_user_target}"

    def _current_user_cache_key(self) -> str:
        return f"{self.user_chat_cache_namespace}:me"

    @staticmethod
    def _graph_user_bind_url(api_base_url: str, user_ref: str) -> str:
        escaped_ref = str(user_ref).replace("'", "''")
        return f"{api_base_url}/users('{escaped_ref}')"

    @staticmethod
    def _graph_user_lookup_url(api_base_url: str, user_ref: str) -> str:
        return f"{api_base_url}/users/{quote(user_ref, safe='')}"

    @staticmethod
    def _graph_user_ref(user_ref: str) -> str:
        normalized = str(user_ref or "").strip().strip("/")
        if normalized.startswith("user:"):
            normalized = normalized.removeprefix("user:").strip()
        if (
            not normalized
            or "/" in normalized
            or any(character.isspace() for character in normalized)
        ):
            raise ValueError(f"Invalid Teams user target {user_ref!r}: {_TEAMS_TARGET_ERROR}")
        return normalized

    @staticmethod
    def _graph_user_payload_id(payload: object) -> str | None:
        if not isinstance(payload, dict):
            return None
        user_id = str(payload.get("id") or "").strip()
        return user_id or None

    @staticmethod
    def _graph_user_payload_mention_text(payload: object, fallback: str) -> str:
        if not isinstance(payload, dict):
            return fallback
        for key in ("displayName", "mail", "userPrincipalName", "id"):
            text = str(payload.get(key) or "").strip()
            if text:
                return text
        return fallback

    def _graph_user_payload_mention(
        self, payload: object, *, user_ref: str
    ) -> _TeamsMentionTarget | None:
        user_id = self._graph_user_payload_id(payload)
        if not user_id:
            return None
        self.user_chat_cache[self._user_id_cache_key(user_ref)] = user_id
        return _TeamsMentionTarget(
            user_ref=user_ref,
            user_id=user_id,
            mention_text=self._graph_user_payload_mention_text(payload, user_ref),
        )

    def resolve_user_id(self, client: httpx.Client, user_ref: str) -> str | None:
        normalized_ref = self._graph_user_ref(user_ref)
        cache_key = self._user_id_cache_key(normalized_ref)
        cached = self.user_chat_cache.get(cache_key)
        if cached:
            return cached

        params = {"$select": "id,userPrincipalName,mail"}
        try:
            response = self._request_with_retries(
                client,
                "GET",
                self._graph_user_lookup_url(self.api_base_url, normalized_ref),
                params=params,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404 or "@" not in normalized_ref:
                raise
        else:
            user_id = self._graph_user_payload_id(response.json())
            if user_id:
                self.user_chat_cache[cache_key] = user_id
                return user_id

        if "@" not in normalized_ref:
            return None

        escaped_ref = normalized_ref.replace("'", "''")
        response = self._request_with_retries(
            client,
            "GET",
            f"{self.api_base_url}/users",
            params={
                **params,
                "$filter": (f"mail eq '{escaped_ref}' or userPrincipalName eq '{escaped_ref}'"),
                "$top": 1,
            },
        )
        payload = response.json()
        if not isinstance(payload, dict):
            return None
        for item in payload.get("value", []):
            user_id = self._graph_user_payload_id(item)
            if user_id:
                self.user_chat_cache[cache_key] = user_id
                return user_id
        return None

    def resolve_user_id_for_reference(self, user_ref: str) -> str | None:
        normalized_target = self._require_allowed_user_target(user_ref)
        with httpx.Client(timeout=self.timeout_seconds) as client:
            return self.resolve_user_id(client, normalized_target.removeprefix("user:"))

    def resolve_user_mention(
        self, client: httpx.Client, user_ref: str
    ) -> _TeamsMentionTarget | None:
        normalized_ref = self._graph_user_ref(user_ref)
        params = {"$select": "id,displayName,userPrincipalName,mail"}
        try:
            response = self._request_with_retries(
                client,
                "GET",
                self._graph_user_lookup_url(self.api_base_url, normalized_ref),
                params=params,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404 or "@" not in normalized_ref:
                raise
        else:
            mention = self._graph_user_payload_mention(
                response.json(),
                user_ref=normalized_ref,
            )
            if mention is not None:
                return mention

        if "@" not in normalized_ref:
            return None

        escaped_ref = normalized_ref.replace("'", "''")
        response = self._request_with_retries(
            client,
            "GET",
            f"{self.api_base_url}/users",
            params={
                **params,
                "$filter": (f"mail eq '{escaped_ref}' or userPrincipalName eq '{escaped_ref}'"),
                "$top": 1,
            },
        )
        payload = response.json()
        if not isinstance(payload, dict):
            return None
        for item in payload.get("value", []):
            mention = self._graph_user_payload_mention(item, user_ref=normalized_ref)
            if mention is not None:
                return mention
        return None

    def _require_allowed_user_target(self, user_ref: str) -> str:
        normalized = normalize_teams_user_target(user_ref)
        if normalized not in set(self._allowed_user_targets()):
            raise ValueError(f"Teams user target is not allowlisted: {normalized}")
        return normalized

    def _current_user_ref(self, client: httpx.Client) -> str:
        cache_key = self._current_user_cache_key()
        cached = self.user_chat_cache.get(cache_key)
        if cached:
            return cached
        response = self._request_with_retries(
            client,
            "GET",
            f"{self.api_base_url}/me",
            params={"$select": "id,userPrincipalName"},
        )
        payload = response.json()
        user_ref = str(payload.get("id") or payload.get("userPrincipalName") or "").strip()
        if not user_ref:
            raise ValueError("Teams Graph /me response did not include id or userPrincipalName.")
        self.user_chat_cache[cache_key] = user_ref
        return user_ref

    def ensure_one_on_one_chat(self, user_ref: str) -> str:
        normalized_user_target = self._require_allowed_user_target(user_ref)
        target_user_ref = normalized_user_target.removeprefix("user:")
        cache_key = self._user_chat_cache_key(normalized_user_target)
        cached_chat_id = self.user_chat_cache.get(cache_key)
        if cached_chat_id:
            return normalize_teams_target(f"chat:{cached_chat_id}")

        with httpx.Client(timeout=self.timeout_seconds) as client:
            current_user_ref = self._current_user_ref(client)
            if current_user_ref.casefold() == target_user_ref.casefold():
                raise ValueError("Cannot create a one-on-one Teams chat with the signed-in user.")
            response = self._request_with_retries(
                client,
                "POST",
                f"{self.api_base_url}/chats",
                json={
                    "chatType": "oneOnOne",
                    "members": [
                        {
                            "@odata.type": "#microsoft.graph.aadUserConversationMember",
                            "roles": ["owner"],
                            "user@odata.bind": self._graph_user_bind_url(
                                self.api_base_url, current_user_ref
                            ),
                        },
                        {
                            "@odata.type": "#microsoft.graph.aadUserConversationMember",
                            "roles": ["owner"],
                            "user@odata.bind": self._graph_user_bind_url(
                                self.api_base_url, target_user_ref
                            ),
                        },
                    ],
                },
            )
        payload = response.json()
        chat_id = str(payload.get("id") or "").strip()
        if not chat_id:
            raise ValueError("Teams Graph create chat response did not include an id.")
        self.user_chat_cache[cache_key] = chat_id
        return normalize_teams_target(f"chat:{chat_id}")

    def _request_with_retries(
        self,
        client: httpx.Client,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        timeout_seconds: float | None = None,
        deadline_monotonic: float | None = None,
        include_auth: bool = True,
        max_response_bytes: int | None = None,
        allow_redirect_response: bool = False,
    ) -> httpx.Response:
        start_time = time.monotonic()
        attempt = 0
        last_error: Exception | None = None
        retry_backoff_seconds = 0.0
        normalized_method = method.upper()
        is_idempotent_request = normalized_method in {
            "DELETE",
            "GET",
            "HEAD",
            "OPTIONS",
            "PUT",
            "TRACE",
        }
        uses_history_retry_budget = is_idempotent_request and deadline_monotonic is not None
        while True:
            effective_timeout = (
                timeout_seconds if timeout_seconds is not None else self.timeout_seconds
            )
            if deadline_monotonic is not None:
                remaining_deadline = deadline_monotonic - time.monotonic()
                if remaining_deadline <= 0:
                    if last_error is not None:
                        raise last_error
                    raise TimeoutError("Teams Graph request deadline expired before an attempt")
                effective_timeout = min(effective_timeout, remaining_deadline)
            attempt_started_at = time.monotonic()
            try:
                request_headers = self._headers() if include_auth else {}
                if max_response_bytes is None:
                    response = client.request(
                        method,
                        url,
                        headers=request_headers,
                        params=params,
                        json=json,
                        timeout=effective_timeout,
                    )
                    if not (allow_redirect_response and response.is_redirect):
                        response.raise_for_status()
                else:
                    request = client.build_request(
                        method,
                        url,
                        headers=request_headers,
                        params=params,
                        json=json,
                        timeout=effective_timeout,
                    )
                    response = _buffer_streaming_response(
                        client.send(request, stream=True),
                        max_bytes=max_response_bytes,
                        allow_redirect=allow_redirect_response,
                    )
                if attempt > 0:
                    _LOGGER.info(
                        "Teams Graph request recovered method=%s path=%s attempts=%d "
                        "duration_seconds=%.3f",
                        method,
                        urlparse(url).path,
                        attempt + 1,
                        time.monotonic() - start_time,
                    )
                return response
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                last_error = exc
                timed_out_unacknowledged_write = (
                    isinstance(exc, httpx.TimeoutException) and not is_idempotent_request
                )
                defer_timeout = isinstance(exc, httpx.TimeoutException) and self.defer_timeout_retry
                if defer_timeout or timed_out_unacknowledged_write or attempt >= self.retry_count:
                    _LOGGER.warning(
                        "Teams Graph request failed method=%s path=%s attempts=%d "
                        "duration_seconds=%.3f error=%s",
                        method,
                        urlparse(url).path,
                        attempt + 1,
                        time.monotonic() - start_time,
                        "timeout" if isinstance(exc, httpx.TimeoutException) else "connect",
                    )
                    raise
                error_class = "timeout" if isinstance(exc, httpx.TimeoutException) else "connect"
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                if status_code not in {429} and not (500 <= status_code <= 599):
                    raise _augment_http_status_error(exc) from exc
                if (
                    status_code == 429
                    and self.defer_retry_after
                    and exc.response.headers.get("Retry-After")
                ):
                    _LOGGER.warning(
                        "Teams Graph request deferred method=%s path=%s attempts=%d "
                        "duration_seconds=%.3f error=http_429",
                        method,
                        urlparse(url).path,
                        attempt + 1,
                        time.monotonic() - start_time,
                    )
                    raise _augment_http_status_error(exc) from exc
                if attempt >= self.retry_count:
                    _LOGGER.warning(
                        "Teams Graph request failed method=%s path=%s attempts=%d "
                        "duration_seconds=%.3f error=http_%d",
                        method,
                        urlparse(url).path,
                        attempt + 1,
                        time.monotonic() - start_time,
                        status_code,
                    )
                    raise _augment_http_status_error(exc) from exc
                last_error = _augment_http_status_error(exc)
                error_class = f"http_{status_code}"

            planned_backoff = min(
                self.retry_max_backoff_seconds,
                self.retry_base_backoff_seconds * (2**attempt),
            )
            jittered_backoff = random.uniform(0.5 * planned_backoff, 1.5 * planned_backoff)
            if uses_history_retry_budget:
                remaining_budget = self.max_total_retry_backoff_seconds - retry_backoff_seconds
            else:
                remaining_budget = self.max_total_retry_backoff_seconds - (
                    time.monotonic() - start_time
                )
            if remaining_budget <= 0:
                if last_error is not None:
                    raise last_error
                raise TimeoutError("Retry budget exhausted before completing Teams API request")
            sleep_for = min(jittered_backoff, remaining_budget)
            deadline_remaining_for_log: float | None = None
            if deadline_monotonic is not None:
                deadline_remaining_for_log = max(0.0, deadline_monotonic - time.monotonic())
                if deadline_remaining_for_log <= 0:
                    if last_error is not None:
                        raise last_error
                    raise TimeoutError("Teams Graph request deadline expired before retry")
                sleep_for = min(sleep_for, deadline_remaining_for_log)
            _LOGGER.warning(
                "Teams Graph request retry method=%s path=%s attempt=%d/%d error=%s "
                "attempt_duration_seconds=%.3f sleep_seconds=%.3f "
                "deadline_remaining_seconds=%s",
                method,
                urlparse(url).path,
                attempt + 1,
                self.retry_count + 1,
                error_class,
                time.monotonic() - attempt_started_at,
                sleep_for,
                (
                    f"{deadline_remaining_for_log:.3f}"
                    if deadline_remaining_for_log is not None
                    else "unbounded"
                ),
            )
            time.sleep(sleep_for)
            retry_backoff_seconds += sleep_for
            attempt += 1

    def _is_graph_api_origin(self, url: str) -> bool:
        parsed_url = urlparse(url)
        parsed_api_base = urlparse(self.api_base_url)
        return (
            parsed_url.scheme.lower() == parsed_api_base.scheme.lower()
            and parsed_url.netloc.lower() == parsed_api_base.netloc.lower()
        )

    @staticmethod
    def _is_sharepoint_content_url(url: str) -> bool:
        parsed_url = urlparse(url)
        host = parsed_url.netloc.lower()
        return any(
            host.endswith(suffix)
            for suffix in (
                ".sharepoint.com",
                ".sharepoint.us",
                ".sharepoint.de",
                ".sharepoint.cn",
                ".sharepoint-df.com",
            )
        )

    def _graph_share_content_url(self, url: str) -> str:
        encoded = base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")
        return f"{self.api_base_url}/shares/u!{encoded}/driveItem/content"

    def _download_graph_content_url(
        self,
        client: httpx.Client,
        url: str,
        *,
        max_bytes: int | None,
    ) -> httpx.Response:
        response = self._request_with_retries(
            client,
            "GET",
            url,
            max_response_bytes=max_bytes,
            allow_redirect_response=True,
        )
        if response.is_redirect:
            redirect_url = str(response.headers.get("location") or "").strip()
            if not redirect_url:
                response.raise_for_status()
            return self._request_with_retries(
                client,
                "GET",
                redirect_url,
                include_auth=False,
                max_response_bytes=max_bytes,
            )
        response.raise_for_status()
        return response

    def _download_attachment_content_url(
        self,
        client: httpx.Client,
        url: str,
        *,
        max_bytes: int | None,
    ) -> httpx.Response:
        parsed_url = urlparse(url)
        if parsed_url.scheme.lower() not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError("Teams attachment contentUrl must be an absolute http(s) URL.")
        if self._is_graph_api_origin(url):
            return self._download_graph_content_url(client, url, max_bytes=max_bytes)
        if self._is_sharepoint_content_url(url):
            return self._download_graph_content_url(
                client,
                self._graph_share_content_url(url),
                max_bytes=max_bytes,
            )
        return self._request_with_retries(
            client,
            "GET",
            url,
            include_auth=False,
            max_response_bytes=max_bytes,
        )

    def new_pagination_budget(self) -> _TeamsGraphPaginationBudget:
        return _TeamsGraphPaginationBudget(
            page_cap=self.history_page_cap,
            deadline_seconds=self.history_deadline_seconds,
            started_at=time.monotonic(),
        )

    @staticmethod
    def _author_id(payload: dict[str, Any]) -> str:
        sender = payload.get("from") or {}
        for source_key in ("user", "application", "device"):
            source = sender.get(source_key) or {}
            candidate = str(source.get("id") or "").strip()
            if candidate:
                return candidate
        return "unknown"

    def _to_envelope(
        self,
        payload: dict[str, Any],
        *,
        target: TeamsTarget,
        source: str = "teams_graph_api",
        session_id: str | None = None,
        reply_to_id: str | None = None,
    ) -> TeamsMessageEnvelope:
        message_id = str(payload.get("id") or "").strip()
        resolved_reply_to_id = str(payload.get("replyToId") or reply_to_id or "").strip() or None
        thread_id = resolved_reply_to_id or (message_id if target.kind == "channel" else None)
        cursor = f"{target.normalized}/{message_id}"
        body = payload.get("body") or {}
        author_id = self._author_id(payload)
        attachments = _teams_payload_attachments(payload)
        safe_payload = _sanitize_message_payload_attachments(payload)
        metadata = {
            "source": source,
            "target": target.normalized,
            "target_type": target.kind,
            "team_id": target.team_id,
            "channel_id": target.channel_id,
            "chat_id": target.chat_id,
            "thread_id": thread_id,
            "author_id": author_id,
            "reply_to_id": resolved_reply_to_id,
            "web_url": str(payload.get("webUrl") or "").strip() or None,
            "content_type": str(body.get("contentType") or "").strip() or None,
            "attachments": attachments,
            "message": safe_payload,
        }
        if session_id is not None:
            metadata["session_id"] = session_id
        return TeamsMessageEnvelope(
            cursor=cursor,
            message_id=message_id,
            author_id=author_id,
            team_id=target.team_id or "",
            channel_id=target.channel_id or "",
            chat_id=target.chat_id,
            thread_id=thread_id,
            content=str(body.get("content") or ""),
            created_at=_parse_graph_datetime(str(payload.get("createdDateTime") or "")),
            metadata=metadata,
        )

    def _iter_channel_messages(
        self,
        client: httpx.Client,
        *,
        team_id: str,
        channel_id: str,
        budget: _TeamsGraphPaginationBudget | None = None,
    ) -> Iterator[dict[str, Any]]:
        if budget is None:
            budget = self.new_pagination_budget()
        next_url = (
            f"{self.api_base_url}/teams/{quote(team_id, safe='')}/channels/"
            f"{quote(channel_id, safe='')}/messages"
        )
        params: dict[str, Any] | None = {"$top": self.poll_limit, "$expand": "replies"}
        seen_urls: set[str] = set()
        while next_url:
            if not budget.can_request_page():
                break
            remaining_seconds = budget.remaining_seconds()
            if remaining_seconds <= 0:
                break
            if next_url in seen_urls:
                raise RuntimeError(
                    f"Teams Graph pagination loop detected for channel {team_id}/{channel_id}"
                )
            seen_urls.add(next_url)
            response = self._request_with_retries(
                client,
                "GET",
                next_url,
                params=params,
                timeout_seconds=min(
                    self.timeout_seconds,
                    self.history_request_timeout_seconds,
                    remaining_seconds,
                ),
                deadline_monotonic=budget.deadline_monotonic,
            )
            budget.record_page()
            payload = response.json()
            for message_payload in payload.get("value", []):
                yield message_payload
            raw_next_url = payload.get("@odata.nextLink")
            next_url = str(raw_next_url).strip() if raw_next_url else ""
            params = None

    def _iter_chat_messages(
        self,
        client: httpx.Client,
        *,
        chat_id: str,
        budget: _TeamsGraphPaginationBudget | None = None,
    ) -> Iterator[dict[str, Any]]:
        if budget is None:
            budget = self.new_pagination_budget()
        next_url = f"{self.api_base_url}/chats/{quote(chat_id, safe='')}/messages"
        params: dict[str, Any] | None = {"$top": self.poll_limit}
        seen_urls: set[str] = set()
        while next_url:
            if not budget.can_request_page():
                break
            remaining_seconds = budget.remaining_seconds()
            if remaining_seconds <= 0:
                break
            if next_url in seen_urls:
                raise RuntimeError(f"Teams Graph pagination loop detected for chat {chat_id}")
            seen_urls.add(next_url)
            response = self._request_with_retries(
                client,
                "GET",
                next_url,
                params=params,
                timeout_seconds=min(
                    self.timeout_seconds,
                    self.history_request_timeout_seconds,
                    remaining_seconds,
                ),
                deadline_monotonic=budget.deadline_monotonic,
            )
            budget.record_page()
            payload = response.json()
            for message_payload in payload.get("value", []):
                yield message_payload
            raw_next_url = payload.get("@odata.nextLink")
            next_url = str(raw_next_url).strip() if raw_next_url else ""
            params = None

    def _iter_one_on_one_chats(
        self,
        client: httpx.Client,
        *,
        budget: _TeamsGraphPaginationBudget | None = None,
    ) -> Iterator[dict[str, Any]]:
        if budget is None:
            budget = self.new_pagination_budget()
        next_url = f"{self.api_base_url}/me/chats"
        params: dict[str, Any] | None = {
            "$filter": "chatType eq 'oneOnOne'",
            "$top": self.poll_limit,
        }
        seen_urls: set[str] = set()
        while next_url:
            if not budget.can_request_page():
                break
            remaining_seconds = budget.remaining_seconds()
            if remaining_seconds <= 0:
                break
            if next_url in seen_urls:
                raise RuntimeError("Teams Graph pagination loop detected for one-on-one chats")
            seen_urls.add(next_url)
            response = self._request_with_retries(
                client,
                "GET",
                next_url,
                params=params,
                timeout_seconds=min(
                    self.timeout_seconds,
                    self.history_request_timeout_seconds,
                    remaining_seconds,
                ),
                deadline_monotonic=budget.deadline_monotonic,
            )
            budget.record_page()
            payload = response.json()
            for chat_payload in payload.get("value", []):
                if isinstance(chat_payload, dict):
                    yield chat_payload
            raw_next_url = payload.get("@odata.nextLink")
            next_url = str(raw_next_url).strip() if raw_next_url else ""
            params = None

    def get_messages(
        self,
        since_cursor: str | None = None,
        before_cursor: str | None = None,
        limit: int | None = None,
        channel_target: str | None = None,
    ) -> list[TeamsMessageEnvelope]:
        if since_cursor and before_cursor:
            raise ValueError("since_cursor and before_cursor are mutually exclusive")
        effective_limit = limit
        if since_cursor is None and before_cursor is None and effective_limit is None:
            effective_limit = self.poll_limit
        messages: dict[str, TeamsMessageEnvelope] = dict(self.message_store.read_all())
        fetched_messages: dict[str, TeamsMessageEnvelope] = {}
        since_cursor_parts = _parse_teams_cursor(since_cursor)
        before_cursor_parts = _parse_teams_cursor(before_cursor)
        requested_target = None
        target_list = self._allowed_channel_targets()
        if channel_target is not None:
            requested_target = normalize_teams_target(channel_target)
            if requested_target not in set(target_list):
                return []
            target_list = [requested_target]
        budget = self.new_pagination_budget()
        with httpx.Client(timeout=self.timeout_seconds) as client:
            for target in target_list:
                if not budget.can_request_page():
                    break
                parsed_target = parse_teams_target(target)
                target_cursor = None
                target_mode = "all"
                if (
                    since_cursor_parts is not None
                    and since_cursor_parts[0] == parsed_target.normalized
                ):
                    target_cursor = since_cursor_parts[1]
                    target_mode = "since"
                elif (
                    before_cursor_parts is not None
                    and before_cursor_parts[0] == parsed_target.normalized
                ):
                    target_cursor = before_cursor_parts[1]
                    target_mode = "before"
                if parsed_target.kind == "user":
                    raise ValueError("Teams user targets must be resolved before reading messages.")
                if parsed_target.kind == "chat":
                    iterator = self._iter_chat_messages(
                        client,
                        chat_id=parsed_target.chat_id or "",
                        budget=budget,
                    )
                else:
                    iterator = self._iter_channel_messages(
                        client,
                        team_id=parsed_target.team_id or "",
                        channel_id=parsed_target.channel_id or "",
                        budget=budget,
                    )
                target_count = 0
                before_seen = target_mode != "before"
                for message_payload in iterator:
                    payloads = [message_payload, *(message_payload.get("replies") or [])]
                    stop_target = False
                    for payload in payloads:
                        envelope = self._to_envelope(payload, target=parsed_target)
                        envelope = self._preserve_status_source_metadata(
                            known=messages.get(envelope.cursor),
                            fetched=envelope,
                        )
                        messages[envelope.cursor] = envelope
                        fetched_messages[envelope.cursor] = envelope

                        if target_mode == "since":
                            if envelope.message_id == target_cursor:
                                stop_target = True
                                break
                            target_count += 1
                            if effective_limit is not None and target_count >= effective_limit:
                                stop_target = True
                                break
                            continue

                        if target_mode == "before":
                            if not before_seen:
                                if envelope.message_id == target_cursor:
                                    before_seen = True
                                continue
                            target_count += 1
                            if effective_limit is not None and target_count >= effective_limit:
                                stop_target = True
                                break
                            continue

                        target_count += 1
                        if effective_limit is not None and target_count >= effective_limit:
                            stop_target = True
                            break

                    if stop_target:
                        break

        _put_teams_messages(self.message_store, fetched_messages)
        ordered = _sorted_teams_messages(messages)
        if requested_target is not None:
            ordered = [
                message
                for message in ordered
                if teams_target_from_message(
                    team_id=message.team_id,
                    channel_id=message.channel_id,
                    chat_id=message.chat_id,
                    metadata=message.metadata,
                )
                == requested_target
            ]
        if before_cursor is not None:
            ordered = [message for message in ordered if message.cursor < before_cursor]
            if effective_limit is not None:
                ordered = ordered[-effective_limit:]
            return ordered
        if effective_limit is not None and since_cursor is None:
            ordered = ordered[-effective_limit:]
        return _filter_teams_messages_since_cursor(ordered, since_cursor)

    @staticmethod
    def _preserve_status_source_metadata(
        *,
        known: TeamsMessageEnvelope | None,
        fetched: TeamsMessageEnvelope,
    ) -> TeamsMessageEnvelope:
        if known is None:
            return fetched
        known_source = str(known.metadata.get("source") or "").strip()
        if known_source != "teams_graph_api_status":
            return fetched
        merged_metadata = dict(fetched.metadata)
        merged_metadata["source"] = "teams_graph_api_status"
        known_session_id = str(known.metadata.get("session_id") or "").strip()
        if known_session_id:
            merged_metadata["session_id"] = known_session_id
        return fetched.model_copy(update={"metadata": merged_metadata})

    def _require_allowed_target(self, channel_target: str) -> TeamsTarget:
        normalized = normalize_teams_target(channel_target)
        allowed = set(self._allowed_channel_targets())
        if allowed and normalized not in allowed:
            raise ValueError(f"Teams target is not allowlisted: {normalized}")
        target = parse_teams_target(normalized)
        if target.kind == "user":
            raise ValueError("Teams user targets must be resolved before this operation.")
        return target

    def _find_message_record(
        self, message_id: str, *, channel_target: str | None = None
    ) -> tuple[TeamsMessageEnvelope, TeamsTarget]:
        requested_target = self._require_allowed_target(channel_target) if channel_target else None
        record = self.message_store.get(message_id)
        if record is None:
            record = next(
                (
                    message
                    for message in self.message_store.read_all().values()
                    if message.message_id == message_id
                ),
                None,
            )
        if record is None:
            raise ValueError(f"Unknown Teams message_id: {message_id}")
        actual_target = parse_teams_target(
            teams_target_from_message(
                team_id=record.team_id,
                channel_id=record.channel_id,
                chat_id=record.chat_id,
                metadata=record.metadata,
            )
        )
        if requested_target is not None and requested_target.normalized != actual_target.normalized:
            raise ValueError("Teams message does not belong to the requested target.")
        self._require_allowed_target(actual_target.normalized)
        return record, actual_target

    def list_attachments(
        self, message_id: str, *, channel_target: str | None = None
    ) -> list[dict[str, Any]]:
        record, target = self._find_message_record(message_id, channel_target=channel_target)
        return self._list_attachments_for_record(record, target, include_remote_hosted=True)

    def _stored_attachment_candidates(self, record: TeamsMessageEnvelope) -> list[dict[str, Any]]:
        raw_attachments = record.metadata.get("attachments")
        if raw_attachments is None and isinstance(record.metadata.get("message"), dict):
            raw_attachments = _teams_payload_attachments(record.metadata["message"])
        return [
            _sanitize_attachment_metadata(item)
            for item in raw_attachments or []
            if isinstance(item, dict)
        ]

    def _record_has_hosted_content_hint(self, record: TeamsMessageEnvelope) -> bool:
        values = [record.content]
        raw_message = record.metadata.get("message")
        if isinstance(raw_message, dict):
            body = raw_message.get("body")
            if isinstance(body, dict):
                values.append(str(body.get("content") or ""))
            values.append(str(raw_message.get("content") or ""))
        for attachment in self._stored_attachment_candidates(record):
            values.append(str(attachment.get("contentUrl") or attachment.get("content_url") or ""))
        return "hostedcontents/" in " ".join(values).lower()

    def _merge_attachment_lists(
        self,
        stored_attachments: list[dict[str, Any]],
        hosted_attachments: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        indexes: dict[str, int] = {}
        for attachment in [*stored_attachments, *hosted_attachments]:
            attachment_id = _teams_attachment_id(attachment)
            if attachment_id and attachment_id in indexes:
                existing = dict(merged[indexes[attachment_id]])
                for key, value in attachment.items():
                    if existing.get(key) in (None, "") and value not in (None, ""):
                        existing[key] = value
                merged[indexes[attachment_id]] = existing
                continue
            if attachment_id:
                indexes[attachment_id] = len(merged)
            merged.append(attachment)
        return merged

    def _list_hosted_content_attachments(
        self,
        record: TeamsMessageEnvelope,
        target: TeamsTarget,
    ) -> list[dict[str, Any]]:
        url = (
            f"{self._teams_message_url(target, record.message_id, thread_id=record.thread_id)}"
            "/hostedContents"
        )
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = self._request_with_retries(client, "GET", url)
        payload = response.json()
        return [
            _teams_hosted_content_attachment(item)
            for item in payload.get("value", [])
            if isinstance(item, dict)
        ]

    def _cache_record_attachments(
        self,
        record: TeamsMessageEnvelope,
        attachments: list[dict[str, Any]],
    ) -> TeamsMessageEnvelope:
        metadata = dict(record.metadata)
        metadata["attachments"] = attachments
        updated = record.model_copy(update={"metadata": metadata})
        self.message_store.put(updated.cursor, updated)
        return updated

    def _list_attachments_for_record(
        self,
        record: TeamsMessageEnvelope,
        target: TeamsTarget,
        *,
        include_remote_hosted: bool,
    ) -> list[dict[str, Any]]:
        stored_attachments = self._stored_attachment_candidates(record)
        if not include_remote_hosted or not self._record_has_hosted_content_hint(record):
            return stored_attachments
        hosted_attachments = self._list_hosted_content_attachments(record, target)
        if not hosted_attachments:
            return stored_attachments
        merged = self._merge_attachment_lists(stored_attachments, hosted_attachments)
        self._cache_record_attachments(record, merged)
        return merged

    def _teams_message_url(
        self, target: TeamsTarget, message_id: str, *, thread_id: str | None = None
    ) -> str:
        if target.kind == "chat":
            return (
                f"{self.api_base_url}/chats/{quote(target.chat_id or '', safe='')}/messages/"
                f"{quote(message_id, safe='')}"
            )
        root_url = (
            f"{self.api_base_url}/teams/{quote(target.team_id or '', safe='')}/channels/"
            f"{quote(target.channel_id or '', safe='')}/messages"
        )
        if thread_id and thread_id != message_id:
            return f"{root_url}/{quote(thread_id, safe='')}/replies/{quote(message_id, safe='')}"
        return f"{root_url}/{quote(message_id, safe='')}"

    def download_attachment(
        self,
        message_id: str,
        attachment_id: str,
        *,
        channel_target: str | None = None,
        max_bytes: int | None = None,
    ) -> dict[str, Any]:
        record, target = self._find_message_record(message_id, channel_target=channel_target)
        attachments = self._list_attachments_for_record(
            record,
            target,
            include_remote_hosted=False,
        )
        attachment = next(
            (item for item in attachments if _teams_attachment_id(item) == attachment_id),
            None,
        )
        if attachment is None and self._record_has_hosted_content_hint(record):
            attachments = self._list_attachments_for_record(
                record,
                target,
                include_remote_hosted=True,
            )
            attachment = next(
                (item for item in attachments if _teams_attachment_id(item) == attachment_id),
                None,
            )
        if attachment is None:
            raise ValueError(f"Unknown Teams attachment: {attachment_id}")
        if attachment.get("contentType") == "forwardedMessageReference":
            raise ValueError("Use teams_get_messages to read forwarded Teams message text.")

        content_url = str(
            attachment.get("contentUrl") or attachment.get("content_url") or ""
        ).strip()
        hosted_content_id = str(attachment.get("hosted_content_id") or attachment_id).strip()
        if (
            attachment.get("attachment_kind") == "hosted_content"
            or _teams_hosted_content_id_from_url(content_url)
            or not content_url
        ):
            url = (
                f"{self._teams_message_url(target, record.message_id, thread_id=record.thread_id)}"
                f"/hostedContents/{quote(hosted_content_id, safe='')}/$value"
            )
        else:
            url = content_url
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = (
                self._request_with_retries(
                    client,
                    "GET",
                    url,
                    max_response_bytes=max_bytes,
                )
                if attachment.get("attachment_kind") == "hosted_content" or not content_url
                else self._download_attachment_content_url(client, url, max_bytes=max_bytes)
            )
            content_type = response.headers.get("content-type") or attachment.get("contentType")
        filename = str(attachment.get("name") or attachment.get("filename") or attachment_id)
        if attachment.get("attachment_kind") == "hosted_content" and not Path(filename).suffix:
            filename = f"{filename}{_teams_content_type_extension(content_type)}"
        return {
            **attachment,
            "filename": filename,
            "mime_type": content_type,
            "content": response.content,
        }

    def _post_file_url(self, target: TeamsTarget, thread_id: str | None) -> str:
        if target.kind == "chat":
            if thread_id:
                raise ValueError("Teams chat targets do not support thread replies.")
            return f"{self.api_base_url}/chats/{quote(target.chat_id or '', safe='')}/messages"
        if target.kind == "user":
            raise ValueError("Teams user targets must be resolved before posting files.")
        encoded_team_id = quote(target.team_id or "", safe="")
        encoded_channel_id = quote(target.channel_id or "", safe="")
        if not thread_id:
            return (
                f"{self.api_base_url}/teams/{encoded_team_id}/channels/"
                f"{encoded_channel_id}/messages"
            )
        return (
            f"{self.api_base_url}/teams/{encoded_team_id}/channels/{encoded_channel_id}/messages/"
            f"{quote(thread_id, safe='')}/replies"
        )

    def _teams_file_message_payload(
        self, message: str, artifacts: list[dict[str, Any]], image_version=None
    ) -> dict[str, Any]:
        hosted_contents: list[dict[str, Any]] = []
        attachments: list[dict[str, Any]] = []
        inline_images: list[tuple[str, str]] = []
        attachment_references: list[str] = []
        download_links: list[tuple[str, str]] = []
        for index, artifact in enumerate(artifacts):
            temporary_id = str(index + 1)
            filename = _artifact_filename(artifact, index=index)
            content_type = _artifact_content_type(artifact, filename)
            if (
                image_version is not None
                and artifact.get("artifact_id") == image_version.artifact_id
            ):
                inline_images.append((image_version.alt_text, image_version.source_url))
                continue
            if _artifact_is_inline_teams_image(content_type):
                hosted_content_url = f"../hostedContents/{temporary_id}/$value"
                hosted_contents.append(
                    {
                        "@microsoft.graph.temporaryId": temporary_id,
                        "contentBytes": base64.b64encode(_artifact_bytes(artifact)).decode("ascii"),
                        "contentType": content_type,
                    }
                )
                inline_images.append((filename, hosted_content_url))
                continue

            reference_content_url = _artifact_teams_reference_content_url(artifact)
            if not reference_content_url:
                download_url = _artifact_control_plane_download_url(artifact)
                if download_url:
                    download_links.append((filename, download_url))
                    continue
                raise ValueError(
                    "Teams file attachments require a SharePoint/OneDrive contentUrl; "
                    "staged bytes can only be sent as inline image hosted content."
                )
            attachments.append(
                {
                    "id": temporary_id,
                    "name": filename,
                    "contentType": "reference",
                    "contentUrl": reference_content_url,
                }
            )
            attachment_references.append(temporary_id)

        body_html = _teams_message_to_html(message)
        for attachment_id in attachment_references:
            body_html += (
                f'<p><attachment id="{html.escape(attachment_id, quote=True)}"></attachment></p>'
            )
        for filename, hosted_content_url in inline_images:
            body_html += (
                '<p><img src="'
                f"{html.escape(hosted_content_url, quote=True)}"
                '" alt="'
                f"{html.escape(filename, quote=True)}"
                '"></p>'
            )
        for filename, download_url in download_links:
            escaped_filename = html.escape(filename, quote=False)
            body_html += (
                f'<p><a href="{html.escape(download_url, quote=True)}">{escaped_filename}</a></p>'
            )
        payload: dict[str, Any] = {
            "body": {"content": body_html, "contentType": "html"},
        }
        if attachments:
            payload["attachments"] = attachments
        if hosted_contents:
            payload["hostedContents"] = hosted_contents
        return payload

    def _resolve_mention_targets(
        self, client: httpx.Client, mentions: list[str] | None
    ) -> list[_TeamsMentionTarget]:
        resolved: list[_TeamsMentionTarget] = []
        seen_user_ids: set[str] = set()
        for user_ref in normalize_teams_mention_refs(mentions):
            mention = self.resolve_user_mention(client, user_ref)
            if mention is None:
                raise ValueError(f"Unable to resolve Teams mention user: {user_ref}")
            if mention.user_id in seen_user_ids:
                continue
            seen_user_ids.add(mention.user_id)
            resolved.append(mention)
        return resolved

    def _teams_status_message_payload(
        self,
        message: str,
        mentions: list[_TeamsMentionTarget],
    ) -> dict[str, Any]:
        body_html = _teams_message_to_html(message)
        payload: dict[str, Any] = {
            "body": {"content": body_html, "contentType": "html"},
        }
        if not mentions:
            return payload

        mention_tags: list[str] = []
        graph_mentions: list[dict[str, Any]] = []
        for index, mention in enumerate(mentions):
            mention_text = mention.mention_text
            mention_tags.append(f'<at id="{index}">{html.escape(mention_text, quote=False)}</at>')
            graph_mentions.append(
                {
                    "id": index,
                    "mentionText": mention_text,
                    "mentioned": {
                        "user": {
                            "id": mention.user_id,
                            "displayName": mention_text,
                            "userIdentityType": "aadUser",
                        }
                    },
                }
            )
        payload["body"]["content"] = f"<p>{' '.join(mention_tags)}</p>{body_html}"
        payload["mentions"] = graph_mentions
        return payload

    def post_file(
        self,
        message: str,
        channel_target: str,
        session_id: str,
        artifacts: list[dict[str, Any]],
        thread_id: str | None = None,
        *,
        image_version=None,
        post_once=False,
    ) -> dict[str, Any]:
        target = self._require_allowed_target(channel_target)
        normalized_thread_id = str(thread_id or "").strip() or None
        url = self._post_file_url(target, normalized_thread_id)
        with httpx.Client(timeout=self.timeout_seconds) as client:
            body = self._teams_file_message_payload(message, artifacts, image_version)
            if post_once:
                # The creation journal owns recovery. Retrying an uncertain POST
                # could create a second progress message.
                response = client.request("POST", url, headers=self._headers(), json=body)
                response.raise_for_status()
            else:
                response = self._request_with_retries(client, "POST", url, json=body)
            payload = response.json()

        envelope = self._to_envelope(
            payload,
            target=target,
            source="teams_graph_api_status",
            session_id=session_id,
            reply_to_id=normalized_thread_id,
        )
        self.message_store.put(envelope.cursor, envelope)
        record_outbound(
            self,
            payload=payload,
            target=target,
            envelope=envelope,
            message=message,
            image_version=image_version,
        )
        result = {
            "status": "posted",
            "channel_target": target.normalized,
            "thread_id": envelope.thread_id,
            "reply_to_message_id": normalized_thread_id,
            "session_id": session_id,
            "message_id": envelope.message_id,
            "web_url": envelope.metadata.get("web_url"),
            "artifact_count": len(artifacts),
            "message_revision": 1,
            "image_editable": self.message_store.get_outbound_receipt(
                target.normalized, envelope.message_id
            ).image_slot
            is not None,
        }
        if target.kind == "chat":
            result["chat_id"] = target.chat_id
        else:
            result["team_id"] = target.team_id
            result["channel_id"] = target.channel_id
        return result

    def post_status(
        self,
        message: str,
        channel_target: str,
        session_id: str,
        thread_id: str | None = None,
        mentions: list[str] | None = None,
    ) -> dict[str, Any]:
        target = parse_teams_target(channel_target)
        if target.kind == "user":
            raise ValueError("Teams user targets must be resolved before posting.")
        normalized_mentions = normalize_teams_mention_refs(mentions)
        normalized_thread_id = str(thread_id or "").strip() or None
        if target.kind == "chat":
            if normalized_thread_id is not None:
                raise ValueError("Teams chat targets do not support thread replies.")
            url = f"{self.api_base_url}/chats/{quote(target.chat_id or '', safe='')}/messages"
        else:
            encoded_team_id = quote(target.team_id or "", safe="")
            encoded_channel_id = quote(target.channel_id or "", safe="")
            if normalized_thread_id is None:
                url = f"{self.api_base_url}/teams/{encoded_team_id}/channels/{encoded_channel_id}/messages"
            else:
                url = (
                    f"{self.api_base_url}/teams/{encoded_team_id}/channels/{encoded_channel_id}/messages/"
                    f"{quote(normalized_thread_id, safe='')}/replies"
                )

        with httpx.Client(timeout=self.timeout_seconds) as client:
            mention_targets = self._resolve_mention_targets(client, normalized_mentions)
            response = self._request_with_retries(
                client,
                "POST",
                url,
                json=self._teams_status_message_payload(message, mention_targets),
            )
            payload = response.json()

        envelope = self._to_envelope(
            payload,
            target=target,
            # Historical name: this marks a self-authored Teams status post from
            # this agent session, not an inbound user-authored Graph message.
            source="teams_graph_api_status",
            session_id=session_id,
            reply_to_id=normalized_thread_id,
        )
        self.message_store.put(envelope.cursor, envelope)
        record_outbound(self, payload=payload, target=target, envelope=envelope, message=message)
        result = {
            "status": "posted",
            "channel_target": target.normalized,
            "thread_id": envelope.thread_id,
            "reply_to_message_id": normalized_thread_id,
            "session_id": session_id,
            "message_id": envelope.message_id,
            "web_url": envelope.metadata.get("web_url"),
            "message_revision": 1,
        }
        if target.kind == "chat":
            result["chat_id"] = target.chat_id
        else:
            result["team_id"] = target.team_id
            result["channel_id"] = target.channel_id
        return result

    def update_message(
        self,
        session_id: str,
        channel_target: str,
        message_id: str,
        message: str,
        thread_id: str | None = None,
        expected_revision: int | None = None,
        *,
        image_version=None,
        clear_image=False,
        verify_image_source=None,
    ) -> dict[str, Any]:
        return update_message(
            self,
            session_id=session_id,
            channel_target=channel_target,
            message_id=message_id,
            message=message,
            thread_id=thread_id,
            expected_revision=expected_revision,
            image_version=image_version,
            clear_image=clear_image,
            verify_image_source=verify_image_source,
        )

    def add_reaction(
        self,
        channel_target: str,
        message_id: str,
        reaction: str,
        session_id: str,
    ) -> dict[str, Any]:
        target = parse_teams_target(channel_target)
        if target.kind == "user":
            raise ValueError("Teams user targets must be resolved before adding reactions.")
        normalized_reaction = str(reaction).strip()
        graph_reaction_type = _normalize_graph_teams_reaction_type(normalized_reaction)
        encoded_message_id = quote(message_id, safe="")
        if target.kind == "chat":
            url = (
                f"{self.api_base_url}/chats/{quote(target.chat_id or '', safe='')}/messages/"
                f"{encoded_message_id}/setReaction"
            )
        else:
            url = (
                f"{self.api_base_url}/teams/{quote(target.team_id or '', safe='')}/channels/"
                f"{quote(target.channel_id or '', safe='')}/messages/{encoded_message_id}/setReaction"
            )
        with httpx.Client(timeout=self.timeout_seconds) as client:
            self._request_with_retries(
                client,
                "POST",
                url,
                json={"reactionType": graph_reaction_type},
            )
        result = {
            "status": "reacted",
            "channel_target": target.normalized,
            "session_id": session_id,
            "message_id": message_id,
            "reaction": normalized_reaction,
            "emoji": normalized_reaction,
        }
        if graph_reaction_type != normalized_reaction:
            result["graph_reaction_type"] = graph_reaction_type
        if target.kind == "chat":
            result["chat_id"] = target.chat_id
        else:
            result["team_id"] = target.team_id
            result["channel_id"] = target.channel_id
        return result
