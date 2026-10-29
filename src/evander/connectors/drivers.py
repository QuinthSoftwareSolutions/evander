from __future__ import annotations

import json
import mimetypes
import os
from pathlib import Path
import re
from time import monotonic
from typing import Any
from urllib.parse import quote, urlencode, urljoin, urlsplit

import httpx

from evander.communication_artifacts import normalize_safe_filename
from evander.connectors.models import (
    CONNECTOR_MAX_INLINE_BYTES,
    ConnectorArtifactResult,
    ConnectorDefinition,
    ConnectorInvocationResult,
    ConnectorOperationDefinition,
    PROTECTED_CONNECTOR_HEADERS,
)
from evander.connectors.registry import ConnectorCallContext
from evander.session_artifacts import open_relative_regular_file_no_follow


_PATH_TEMPLATE_FIELD = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
_HTTP_HEADER_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


class ConnectorInvocationError(RuntimeError):
    pass


class ConnectorUpstreamAuthenticationError(ConnectorInvocationError):
    pass


def _artifact_result(metadata: Any) -> ConnectorArtifactResult:
    return ConnectorArtifactResult(
        artifact_id=metadata.artifact_id,
        filename=metadata.filename,
        mime_type=metadata.mime_type,
        byte_count=metadata.byte_count,
        sha256=metadata.sha256,
        expires_at=metadata.expires_at.isoformat(),
    )


def _render_path_template(template: str, arguments: dict[str, Any]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in arguments:
            raise ConnectorInvocationError(f"missing path argument: {name}")
        value = arguments[name]
        if isinstance(value, (dict, list)) or value is None:
            raise ConnectorInvocationError(f"path argument {name!r} must be a scalar")
        return quote(str(value), safe="")

    rendered = _PATH_TEMPLATE_FIELD.sub(replace, template)
    if "{" in rendered or "}" in rendered:
        raise ConnectorInvocationError("path template contains an invalid placeholder")
    return rendered


def _render_filesystem_path_template(template: str, arguments: dict[str, Any]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in arguments:
            raise ConnectorInvocationError(f"missing path argument: {name}")
        value = arguments[name]
        if isinstance(value, (dict, list)) or value is None:
            raise ConnectorInvocationError(f"path argument {name!r} must be a scalar")
        text = str(value)
        if text in {".", ".."} or "/" in text or "\\" in text or "\0" in text:
            raise ConnectorInvocationError(
                "connector file is outside the approved root: filesystem path arguments "
                "must not contain path separators or traversal"
            )
        return text

    rendered = _PATH_TEMPLATE_FIELD.sub(replace, template)
    if "{" in rendered or "}" in rendered:
        raise ConnectorInvocationError("path template contains an invalid placeholder")
    return rendered


class RestConnectorDriver:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    def validate_definition(self, definition: ConnectorDefinition) -> None:
        base_url = str(definition.config.get("base_url") or "").strip().rstrip("/")
        parts = urlsplit(base_url)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValueError("REST connector config.base_url must be an absolute http(s) URL")
        if parts.username or parts.password or parts.fragment or parts.query:
            raise ValueError(
                "REST connector base_url must not contain credentials, query, or fragment"
            )
        fixed_headers = definition.config.get("headers", {})
        if not isinstance(fixed_headers, dict):
            raise ValueError("REST connector config.headers must be an object")
        for name, value in fixed_headers.items():
            header_name = str(name or "")
            normalized = header_name.strip().lower()
            if not normalized or normalized in PROTECTED_CONNECTOR_HEADERS:
                raise ValueError(f"REST connector header {name!r} is reserved")
            if not _HTTP_HEADER_NAME.fullmatch(header_name):
                raise ValueError(f"REST connector header {name!r} must be a valid HTTP field name")
            if not isinstance(value, str):
                raise ValueError("REST connector fixed header values must be strings")
            try:
                encoded_value = value.encode("ascii")
            except UnicodeEncodeError as exc:
                raise ValueError(
                    f"REST connector header {name!r} value must contain only ASCII characters"
                ) from exc
            if (
                value[:1] in {" ", "\t"}
                or value[-1:] in {" ", "\t"}
                or any(byte == 127 or (byte < 32 and byte != 9) for byte in encoded_value)
            ):
                raise ValueError(
                    f"REST connector header {name!r} value contains invalid HTTP characters"
                )
        for operation_name, operation in definition.operations.items():
            if operation.disabled:
                continue
            config = operation.config
            method = str(config.get("method") or "").strip().upper()
            if method not in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"}:
                raise ValueError(
                    f"REST connector operation {operation_name!r} has unsupported method"
                )
            path = str(config.get("path") or "").strip()
            if not path.startswith("/"):
                raise ValueError(
                    f"REST connector operation {operation_name!r} path must start with /"
                )
            query = config.get("query", {})
            if not isinstance(query, dict) or any(
                not str(key).strip() or not str(value).strip() for key, value in query.items()
            ):
                raise ValueError("REST connector operation query must map argument to query name")
            body_argument = config.get("json_body_argument")
            if body_argument is not None and not str(body_argument).strip():
                raise ValueError("json_body_argument must be a non-empty argument name")

    async def invoke(
        self,
        *,
        context: ConnectorCallContext,
        connector_id: str,
        definition: ConnectorDefinition,
        operation_name: str,
        operation: ConnectorOperationDefinition,
        arguments: dict[str, Any],
    ) -> ConnectorInvocationResult:
        started = monotonic()
        config = operation.config
        method = str(config["method"]).upper()
        rendered_path = _render_path_template(str(config["path"]), arguments)
        base_url = str(definition.config["base_url"]).rstrip("/")
        url = f"{base_url}{rendered_path}"
        query_items: list[tuple[str, str]] = []
        for argument_name, query_name in dict(config.get("query") or {}).items():
            if argument_name not in arguments or arguments[argument_name] is None:
                continue
            value = arguments[argument_name]
            values = value if isinstance(value, list) else [value]
            for item in values:
                if isinstance(item, dict):
                    raise ConnectorInvocationError(
                        f"query argument {argument_name!r} must contain scalar values"
                    )
                query_items.append((str(query_name), str(item)))
        if query_items:
            url = f"{url}?{urlencode(query_items, doseq=True)}"
        headers = {
            str(name): str(value)
            for name, value in dict(definition.config.get("headers") or {}).items()
        }
        headers.update(context.auth_headers)
        json_body = None
        body_argument = str(config.get("json_body_argument") or "").strip()
        if body_argument:
            if body_argument not in arguments:
                raise ConnectorInvocationError(f"missing JSON body argument: {body_argument}")
            json_body = arguments[body_argument]
        max_bytes = int(operation.max_response_bytes or 0)
        response: httpx.Response | None = None
        async with httpx.AsyncClient(
            timeout=operation.timeout_seconds,
            transport=self.transport,
            follow_redirects=False,
        ) as client:
            current_url = url
            current_method = method
            current_json_body = json_body
            for redirect_count in range(4):
                async with client.stream(
                    current_method,
                    current_url,
                    headers=headers,
                    json=current_json_body,
                ) as streamed_response:
                    if streamed_response.is_redirect and bool(
                        config.get("follow_same_origin_redirects", False)
                    ):
                        location = streamed_response.headers.get("location")
                        if not location or redirect_count >= 3:
                            response = httpx.Response(
                                streamed_response.status_code,
                                headers=streamed_response.headers,
                                request=httpx.Request(current_method, current_url),
                            )
                            break
                        next_url = urljoin(str(streamed_response.url), location)
                        if _origin(next_url) != _origin(base_url):
                            raise ConnectorInvocationError(
                                "connector redirect left the configured origin"
                            )
                        current_url = next_url
                        if streamed_response.status_code == 303 and current_method != "HEAD":
                            current_method = "GET"
                            current_json_body = None
                        continue
                    declared_length = streamed_response.headers.get("content-length")
                    if declared_length:
                        try:
                            if int(declared_length) > max_bytes:
                                raise ConnectorInvocationError(
                                    "connector response exceeded max_response_bytes"
                                )
                        except ValueError:
                            pass
                    content = bytearray()
                    async for chunk in streamed_response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > max_bytes:
                            raise ConnectorInvocationError(
                                "connector response exceeded max_response_bytes"
                            )
                    response = httpx.Response(
                        streamed_response.status_code,
                        headers=streamed_response.headers,
                        content=bytes(content),
                        request=httpx.Request(current_method, str(streamed_response.url)),
                    )
                    break
        if response is None:
            raise ConnectorInvocationError("connector upstream did not return a response")
        if response.status_code == 401:
            raise ConnectorUpstreamAuthenticationError("connector upstream rejected authentication")
        if response.is_redirect:
            raise ConnectorInvocationError("connector upstream redirect was not allowed")
        if response.status_code < 200 or response.status_code >= 300:
            raise ConnectorInvocationError(
                f"connector upstream returned HTTP {response.status_code}"
            )
        content = response.content
        if context.sensitive_values:
            from evander.credentials.sanitize import reject_reflected_credentials
            reject_reflected_credentials(content, context.sensitive_values)
            reject_reflected_credentials(dict(response.headers), context.sensitive_values)
        duration_ms = int((monotonic() - started) * 1000)
        if operation.result_mode == "json":
            try:
                value = json.loads(content)
                if context.sensitive_values:
                    reject_reflected_credentials(value, context.sensitive_values)
            except (UnicodeDecodeError, ValueError) as exc:
                raise ConnectorInvocationError("connector response was not valid JSON") from exc
            return ConnectorInvocationResult(
                connector_id=connector_id,
                operation=operation_name,
                result_mode="json",
                value=value,
                upstream_status=response.status_code,
                duration_ms=duration_ms,
            )
        if operation.result_mode == "text":
            if len(content) > CONNECTOR_MAX_INLINE_BYTES:
                raise ConnectorInvocationError("connector text response exceeded inline limit")
            return ConnectorInvocationResult(
                connector_id=connector_id,
                operation=operation_name,
                result_mode="text",
                value=response.text,
                upstream_status=response.status_code,
                duration_ms=duration_ms,
            )
        filename = _response_filename(response, operation_name)
        mime_type = response.headers.get("content-type", "").split(";", 1)[0].strip() or None
        metadata = context.artifact_store.stage(
            provider=f"connector:{connector_id}",
            session_id=context.session_id,
            content=content,
            original_filename=filename,
            mime_type=mime_type,
            metadata={"connector_id": connector_id, "operation": operation_name},
        )
        return ConnectorInvocationResult(
            connector_id=connector_id,
            operation=operation_name,
            result_mode="artifact",
            artifact=_artifact_result(metadata),
            upstream_status=response.status_code,
            duration_ms=duration_ms,
        )


class FilesystemConnectorDriver:
    def validate_definition(self, definition: ConnectorDefinition) -> None:
        root = Path(str(definition.config.get("root") or ""))
        if not root.is_absolute():
            raise ValueError("filesystem connector config.root must be an absolute path")
        if definition.auth_profile is not None:
            raise ValueError("filesystem connectors do not use auth_profile")
        for operation_name, operation in definition.operations.items():
            if operation.disabled:
                continue
            path = str(operation.config.get("path") or "").strip()
            if not path or path.startswith("/"):
                raise ValueError(f"filesystem operation {operation_name!r} path must be relative")
            if operation.result_mode != "artifact":
                raise ValueError("filesystem operations must use result_mode='artifact'")
            if operation.access != "read":
                raise ValueError("filesystem operations must use access='read'")

    async def invoke(
        self,
        *,
        context: ConnectorCallContext,
        connector_id: str,
        definition: ConnectorDefinition,
        operation_name: str,
        operation: ConnectorOperationDefinition,
        arguments: dict[str, Any],
    ) -> ConnectorInvocationResult:
        started = monotonic()
        configured_root = Path(str(definition.config["root"]))
        try:
            root = configured_root.resolve(strict=True)
        except OSError as exc:
            raise ConnectorInvocationError("configured connector root is unavailable") from exc
        rendered = _render_filesystem_path_template(str(operation.config["path"]), arguments)
        relative = Path(rendered.replace("\\", "/"))
        if relative.is_absolute() or ".." in relative.parts:
            raise ConnectorInvocationError("connector file path must remain relative")
        try:
            fd = open_relative_regular_file_no_follow(
                root,
                relative.as_posix(),
                flags=os.O_RDONLY,
                path_label="connector file path",
                unsafe_path_message=(
                    "connector file path must not contain symlinks or leave the approved root"
                ),
            )
        except (OSError, ValueError) as exc:
            raise ConnectorInvocationError("connector file is outside the approved root") from exc
        if fd is None:
            raise ConnectorInvocationError("connector file must be a regular file")
        max_bytes = int(operation.max_response_bytes or 0)
        with os.fdopen(fd, "rb") as handle:
            size = os.fstat(handle.fileno()).st_size
            if size > max_bytes:
                raise ConnectorInvocationError(
                    f"connector file exceeded max_response_bytes: {size} > {max_bytes}"
                )
            content = handle.read(max_bytes + 1)
            if len(content) > max_bytes:
                raise ConnectorInvocationError(
                    f"connector file exceeded max_response_bytes: > {max_bytes}"
                )
        metadata = context.artifact_store.stage(
            provider=f"connector:{connector_id}",
            session_id=context.session_id,
            content=content,
            original_filename=relative.name,
            mime_type=mimetypes.guess_type(relative.name)[0],
            metadata={"connector_id": connector_id, "operation": operation_name},
        )
        return ConnectorInvocationResult(
            connector_id=connector_id,
            operation=operation_name,
            result_mode="artifact",
            artifact=_artifact_result(metadata),
            duration_ms=int((monotonic() - started) * 1000),
        )


def _origin(url: str) -> tuple[str, str, int | None]:
    parts = urlsplit(url)
    return parts.scheme.lower(), (parts.hostname or "").lower(), parts.port


def _response_filename(response: httpx.Response, fallback: str) -> str:
    disposition = response.headers.get("content-disposition", "")
    match = re.search(r"filename\*?=(?:UTF-8''|\")?([^\";]+)", disposition, re.IGNORECASE)
    if match:
        return normalize_safe_filename(match.group(1).strip())
    name = Path(response.url.path).name
    return normalize_safe_filename(name or fallback)
