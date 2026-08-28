from __future__ import annotations

from collections.abc import Mapping
import os
from typing import Any

from evander.models import DirectoryPath

from .client import ElasticStackClient
from .models import settings_from_provider_state


def client_for_session(
    runtime: Any,
    session: Any,
    *,
    transport: Any | None = None,
) -> ElasticStackClient:
    config = _config_for_session(runtime, session)
    provider_state = _provider_state(config)
    metadata = getattr(session, "metadata", {})
    return ElasticStackClient(
        settings_from_provider_state(provider_state, session_metadata=metadata),
        secret_resolver=lambda ref: os.environ.get(ref),
        transport=transport,
    )


def client_for_provider_context(
    context: Any,
    *,
    session_id: str | None = None,
    transport: Any | None = None,
) -> ElasticStackClient:
    session = _session_from_context(context, session_id) if session_id else None
    config = _config_for_session(context, session) if session is not None else None
    provider_state = (
        _provider_state(config)
        if config is not None
        else _provider_state(getattr(context, "provider_config", {}))
    )
    metadata = getattr(session, "metadata", {}) if session is not None else {}
    secret_resolver = getattr(context, "resolve_secret", None)
    if not callable(secret_resolver):

        def secret_resolver(ref: str) -> str | None:
            return os.environ.get(ref)

    return ElasticStackClient(
        settings_from_provider_state(provider_state, session_metadata=metadata),
        secret_resolver=secret_resolver,
        transport=transport,
    )


def _config_for_session(runtime_or_context: Any, session: Any) -> Any:
    resolver = getattr(runtime_or_context, "provider_config_resolver", None)
    if resolver is None:
        resolver = getattr(runtime_or_context, "provider_config", None)
    resolve = getattr(resolver, "resolve_for_session_manifest", None)
    if callable(resolve):
        return resolve(session)
    config = getattr(runtime_or_context, "config", None)
    if config is not None:
        return config
    return getattr(runtime_or_context, "provider_config", {})


def _provider_state(config: Any) -> dict[str, Any]:
    if isinstance(config, Mapping):
        state = config.get("provider_state", {})
    else:
        state = getattr(config, "provider_state", {})
    if state is None:
        return {}
    if not isinstance(state, Mapping):
        raise ValueError("provider_state must be an object")
    return dict(state)


def _session_from_context(context: Any, session_id: str) -> Any:
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        raise ValueError("session_id must be non-empty")
    lookup = getattr(context, "session_lookup", None)
    read_all = getattr(lookup, "read_all_sessions", None)
    if not callable(read_all):
        raise RuntimeError("Elastic Stack workflow provider requires session_lookup")
    records = read_all()
    if isinstance(records, Mapping):
        session = records.get(normalized_session_id)
    else:
        session = next(
            (
                item
                for item in records or ()
                if str(_record_value(item, "session_id", "") or "") == normalized_session_id
            ),
            None,
        )
    if session is None:
        raise ValueError(f"session {normalized_session_id!r} was not found")
    workflow_directory_path = _directory_path(
        _record_value(getattr(context, "metadata", {}), "directory_path", "root")
    )
    session_directory_path = _directory_path(_record_value(session, "directory_path", "root"))
    if session_directory_path != workflow_directory_path and not session_directory_path.startswith(
        f"{workflow_directory_path}/"
    ):
        raise ValueError(
            f"session {normalized_session_id!r} is outside workflow directory scope "
            f"{workflow_directory_path!r}"
        )
    return session


def _record_value(record: Any, key: str, default: Any = None) -> Any:
    if isinstance(record, Mapping):
        return record.get(key, default)
    return getattr(record, key, default)


def _directory_path(value: Any) -> str:
    return DirectoryPath(segments=str(value or "root")).as_posix()
