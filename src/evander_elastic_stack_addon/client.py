from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
from fnmatch import fnmatchcase
import json
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from .models import (
    ElasticStackAuthProfile,
    ElasticStackEndpoint,
    ElasticStackSettings,
    ElasticStackTarget,
    _validate_import_options,
    canonical_http_origin,
    normalize_identifier,
    normalize_space_id,
)


class ElasticStackConfigError(RuntimeError):
    """Raised when an Elastic Stack target or host secret is unavailable."""


class ElasticStackApiError(RuntimeError):
    """Raised when Elasticsearch or Kibana rejects a bounded operation."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__(message)


class ElasticStackClient:
    def __init__(
        self,
        settings: ElasticStackSettings,
        *,
        secret_resolver: Callable[[str], str | None],
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.secret_resolver = secret_resolver
        self.transport = transport

    def list_targets(self) -> list[dict[str, Any]]:
        return [
            target.redacted_payload(target_id=target_id)
            for target_id, target in sorted(self.settings.targets.items())
        ]

    def kibana_status(self, target_id: str) -> dict[str, Any]:
        return self._json_request(
            endpoint=self._endpoint(target_id, "kibana"),
            method="GET",
            path="/api/status",
        )

    def kibana_list_data_views(
        self,
        target_id: str,
        *,
        space_id: str | None = None,
    ) -> dict[str, Any]:
        return self._json_request(
            endpoint=self._endpoint(target_id, "kibana"),
            method="GET",
            path=self._kibana_path("/api/data_views", space_id),
        )

    def kibana_find_saved_objects(
        self,
        target_id: str,
        *,
        types: list[str],
        search: str | None = None,
        page: int = 1,
        per_page: int = 100,
        space_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_types = self._saved_object_types(types)
        query: list[tuple[str, str]] = [("type", item) for item in normalized_types]
        query.extend((("page", str(max(1, int(page)))), ("per_page", str(int(per_page)))))
        if search is not None and str(search).strip():
            query.extend((("search", str(search)), ("search_fields", "title")))
        return self._json_request(
            endpoint=self._endpoint(target_id, "kibana"),
            method="GET",
            path=(
                f"{self._kibana_path('/api/saved_objects/_find', space_id)}?"
                f"{urlencode(query, doseq=True)}"
            ),
        )

    def kibana_upsert_data_view(
        self,
        target_id: str,
        *,
        data_view: Mapping[str, Any],
        override: bool = True,
        space_id: str | None = None,
    ) -> dict[str, Any]:
        payload = dict(data_view)
        if not str(payload.get("title") or "").strip():
            raise ValueError("data_view.title is required")
        return self._json_request(
            endpoint=self._endpoint(target_id, "kibana"),
            method="POST",
            path=self._kibana_path("/api/data_views/data_view", space_id),
            json_body={"data_view": payload, "override": bool(override)},
            kibana_write=True,
        )

    def kibana_set_default_data_view(
        self,
        target_id: str,
        *,
        data_view_id: str | None,
        force: bool = True,
        space_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_id = str(data_view_id).strip() if data_view_id is not None else None
        return self._json_request(
            endpoint=self._endpoint(target_id, "kibana"),
            method="POST",
            path=self._kibana_path("/api/data_views/default", space_id),
            json_body={"data_view_id": normalized_id or None, "force": bool(force)},
            kibana_write=True,
        )

    def kibana_import_saved_objects(
        self,
        target_id: str,
        *,
        ndjson: str,
        overwrite: bool = True,
        create_new_copies: bool = False,
        compatibility_mode: bool = False,
        space_id: str | None = None,
    ) -> dict[str, Any]:
        _validate_import_options(
            overwrite=overwrite,
            create_new_copies=create_new_copies,
            compatibility_mode=compatibility_mode,
        )
        content, object_count = self._validated_ndjson(ndjson)
        query = urlencode(
            {
                "overwrite": str(bool(overwrite)).lower(),
                "createNewCopies": str(bool(create_new_copies)).lower(),
                "compatibilityMode": str(bool(compatibility_mode)).lower(),
            }
        )
        endpoint = self._endpoint(target_id, "kibana")
        response = self._request(
            endpoint=endpoint,
            method="POST",
            path=f"{self._kibana_path('/api/saved_objects/_import', space_id)}?{query}",
            files={"file": ("saved-objects.ndjson", content, "application/ndjson")},
            kibana_write=True,
        )
        result = self._response_json(response)
        result.setdefault("submitted_object_count", object_count)
        return result

    def elasticsearch_cluster_health(self, target_id: str) -> dict[str, Any]:
        return self._json_request(
            endpoint=self._endpoint(target_id, "elasticsearch"),
            method="GET",
            path="/_cluster/health",
        )

    def elasticsearch_list_indices(
        self,
        target_id: str,
        *,
        index_pattern: str = "*",
        include_hidden: bool = False,
    ) -> list[dict[str, Any]]:
        pattern = self._index_pattern(index_pattern)
        params = {
            "format": "json",
            "h": "health,status,index,uuid,pri,rep,docs.count,store.size",
            "expand_wildcards": "all" if include_hidden else "open,closed",
        }
        result = self._json_request(
            endpoint=self._endpoint(target_id, "elasticsearch"),
            method="GET",
            path=f"/_cat/indices/{quote(pattern, safe='*,-_.*')}?{urlencode(params)}",
        )
        if not isinstance(result, list):
            raise ElasticStackApiError("Elasticsearch returned an invalid indices response")
        return [dict(item) for item in result if isinstance(item, Mapping)]

    def elasticsearch_get_mappings(
        self,
        target_id: str,
        *,
        index_pattern: str,
    ) -> dict[str, Any]:
        pattern = self._index_pattern(index_pattern)
        return self._json_request(
            endpoint=self._endpoint(target_id, "elasticsearch"),
            method="GET",
            path=f"/{quote(pattern, safe='*,-_.*')}/_mapping",
        )

    def _endpoint(self, target_id: str, endpoint_name: str) -> ElasticStackEndpoint:
        normalized_target_id = normalize_identifier(target_id, field_name="target_id")
        target: ElasticStackTarget | None = self.settings.targets.get(normalized_target_id)
        if target is None:
            raise ElasticStackConfigError(
                f"Elastic Stack target {normalized_target_id!r} is unknown"
            )
        endpoint = getattr(target, endpoint_name)
        if endpoint is None:
            raise ElasticStackConfigError(
                f"Elastic Stack target {normalized_target_id!r} has no {endpoint_name} endpoint"
            )
        return endpoint

    def _json_request(
        self,
        *,
        endpoint: ElasticStackEndpoint,
        method: str,
        path: str,
        json_body: Any | None = None,
        kibana_write: bool = False,
    ) -> Any:
        return self._response_json(
            self._request(
                endpoint=endpoint,
                method=method,
                path=path,
                json_body=json_body,
                kibana_write=kibana_write,
            )
        )

    def _request(
        self,
        *,
        endpoint: ElasticStackEndpoint,
        method: str,
        path: str,
        json_body: Any | None = None,
        files: dict[str, Any] | None = None,
        kibana_write: bool = False,
    ) -> httpx.Response:
        if not path.startswith("/"):
            raise ValueError("Elastic Stack request path must start with /")
        profile = self.settings.auth_profiles.get(endpoint.auth_profile)
        if profile is None:
            raise ElasticStackConfigError(
                f"Elastic Stack auth profile {endpoint.auth_profile!r} is unavailable"
            )
        self._ensure_origin_allowed(endpoint.base_url, profile)
        headers = self._auth_headers(profile)
        headers["accept"] = "application/json"
        if kibana_write:
            headers["kbn-xsrf"] = "evander-elastic-stack-addon"
        url = f"{endpoint.base_url.rstrip('/')}{path}"
        try:
            with httpx.Client(
                timeout=self.settings.timeout_seconds,
                transport=self.transport,
                follow_redirects=False,
            ) as client:
                with client.stream(
                    method,
                    url,
                    headers=headers,
                    json=json_body,
                    files=files,
                ) as streamed:
                    content = bytearray()
                    for chunk in streamed.iter_bytes():
                        content.extend(chunk)
                        if len(content) > self.settings.max_response_bytes:
                            raise ElasticStackApiError(
                                "Elastic Stack response exceeded max_response_bytes"
                            )
                    response = httpx.Response(
                        streamed.status_code,
                        headers=streamed.headers,
                        content=bytes(content),
                        request=httpx.Request(method, url),
                    )
        except httpx.RequestError as exc:
            raise ElasticStackApiError("Elastic Stack request failed") from exc
        if response.is_redirect:
            raise ElasticStackApiError(
                "Elastic Stack redirect was not allowed", status_code=response.status_code
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise ElasticStackApiError(
                f"Elastic Stack upstream returned HTTP {response.status_code}",
                status_code=response.status_code,
            )
        return response

    def _auth_headers(self, profile: ElasticStackAuthProfile) -> dict[str, str]:
        if profile.type == "none":
            return {}
        if profile.type == "basic_env":
            username = self._secret(profile.username_env)
            password = self._secret(profile.password_env)
            encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
            return {"authorization": f"Basic {encoded}"}
        token = self._secret(profile.token_env)
        scheme = "ApiKey" if profile.type == "api_key_env" else "Bearer"
        return {"authorization": f"{scheme} {token}"}

    def _secret(self, reference: str | None) -> str:
        normalized = str(reference or "").strip()
        value = str(self.secret_resolver(normalized) or "").strip() if normalized else ""
        if not normalized or not value:
            raise ElasticStackConfigError("Elastic Stack authentication is unavailable")
        return value

    @staticmethod
    def _ensure_origin_allowed(base_url: str, profile: ElasticStackAuthProfile) -> None:
        origin = canonical_http_origin(base_url, field_name="base_url")
        if any(
            origin == pattern or ("*" in pattern and fnmatchcase(origin, pattern))
            for pattern in profile.allowed_origins
        ):
            return
        raise ElasticStackConfigError(
            f"Elastic Stack target origin {origin!r} is not allowed by its auth profile"
        )

    def _validated_ndjson(self, ndjson: str) -> tuple[bytes, int]:
        if not isinstance(ndjson, str) or not ndjson.strip():
            raise ValueError("ndjson must be a non-empty string")
        content = ndjson.encode("utf-8")
        if len(content) > self.settings.max_import_bytes:
            raise ValueError("ndjson exceeds max_import_bytes")
        object_count = 0
        for line_number, raw_line in enumerate(ndjson.splitlines(), start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except ValueError as exc:
                raise ValueError(f"ndjson line {line_number} is not valid JSON") from exc
            if not isinstance(item, dict):
                raise ValueError(f"ndjson line {line_number} must contain an object")
            object_count += 1
            if object_count > self.settings.max_import_objects:
                raise ValueError("ndjson exceeds max_import_objects")
        if object_count == 0:
            raise ValueError("ndjson must contain at least one object")
        if not content.endswith(b"\n"):
            content += b"\n"
        return content, object_count

    @staticmethod
    def _response_json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            raise ElasticStackApiError("Elastic Stack returned a non-JSON response") from exc

    @staticmethod
    def _kibana_path(path: str, space_id: str | None) -> str:
        normalized_space = normalize_space_id(space_id)
        if normalized_space is None:
            return path
        return f"/s/{quote(normalized_space, safe='')}{path}"

    @staticmethod
    def _index_pattern(value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("index_pattern must be non-empty")
        if any(character in normalized for character in ("/", "\\", "?", "#")):
            raise ValueError("index_pattern contains unsupported characters")
        return normalized

    @staticmethod
    def _saved_object_types(values: list[str]) -> list[str]:
        if not isinstance(values, list):
            raise ValueError("types must be a list")
        result: list[str] = []
        for value in values:
            normalized = normalize_identifier(value, field_name="saved object type")
            result.append(normalized)
        if not result:
            raise ValueError("types must include at least one saved object type")
        return list(dict.fromkeys(result))
