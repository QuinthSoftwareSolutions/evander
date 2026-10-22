from __future__ import annotations

import fcntl
import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evander.codex_auth import (
    DEFAULT_SUBSCRIPTION_PROFILE_ID,
    CodexAuthMode,
    effective_codex_auth_mode,
    effective_subscription_profile_id,
    normalize_subscription_profile_id,
)
from evander.execution.container_worker_client import (
    ContainerWorkerClient,
    ContainerWorkerClientError,
)
from evander.models import SessionManifest
from evander.repositories.session_manifests import SessionManifestRepository


@contextmanager
def subscription_profile_binding_lock(
    runtime_dir: Path, worker_url: object, profile_id: object
) -> Iterator[None]:
    """Serialize profile deletion with binding validation and persistence across processes."""
    profile = normalize_subscription_profile_id(profile_id, allow_none=False)
    worker = str(worker_url or "").strip().rstrip("/")
    key = hashlib.sha256(f"{worker}\0{profile}".encode()).hexdigest()
    path = Path(runtime_dir) / "subscription-profile-locks" / f"{key}.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep the file after unlocking so waiting processes always lock the same inode.
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _is_container_session(session: SessionManifest) -> bool:
    return session.vm_provider == "container_worker_vm" or (
        session.metadata.get("execution_backend") == "container_worker_vm"
    )


def _profile_for_session(session: SessionManifest, config: Any) -> str | None:
    if not _is_container_session(session):
        return None
    if session.subscription_profile_id:
        return effective_subscription_profile_id(session=session)
    metadata = session.metadata or {}
    mode = str(
        metadata.get("model_provider_mode") or getattr(config, "model_provider_mode", "")
    ).strip()
    if (
        mode != "openai_public"
        or str(metadata.get("agent_provider") or "codex").strip().casefold() != "codex"
    ):
        return None
    if (
        effective_codex_auth_mode(session=session, config=config)
        is CodexAuthMode.CHATGPT_SUBSCRIPTION
    ):
        return DEFAULT_SUBSCRIPTION_PROFILE_ID
    return None


def save_session_with_subscription_profile(
    *,
    config: Any,
    repository: SessionManifestRepository,
    session: SessionManifest,
    client: Any | None = None,
    provider_config: Any | None = None,
) -> None:
    """Revalidate a new live binding under the same lock used by profile deletion."""
    if provider_config is not None:
        config = provider_config.resolve_for_directory(session.directory_path)
        resolve_session = getattr(provider_config, "resolve_for_session_id", None)
        if (
            client is None
            and _is_container_session(session)
            and session.codex_auth_mode is not CodexAuthMode.METERED_API
            and callable(resolve_session)
        ):
            config = resolve_session(session.session_id)
    now = datetime.now(UTC)
    profile_id = _profile_for_session(session, config)
    if profile_id is None or session.expires_at <= now:
        repository.upsert_session(session)
        return
    worker_url = (
        str(
            getattr(client, "base_url", None)
            or session.metadata.get("worker_url")
            or getattr(config, "container_worker_base_url", None)
            or ""
        )
        .strip()
        .rstrip("/")
    )
    with subscription_profile_binding_lock(config.runtime_dir, worker_url, profile_id):
        now = datetime.now(UTC)
        previous = repository.get_session(session.session_id)
        previous_worker = (
            str((previous.metadata if previous else {}).get("worker_url") or "").strip().rstrip("/")
        )
        retains_live_binding = (
            previous is not None
            and previous.expires_at > now
            and _profile_for_session(previous, config) == profile_id
            and (not previous_worker or previous_worker == worker_url)
        )
        if not retains_live_binding:
            if client is None:
                client = ContainerWorkerClient(
                    base_url=worker_url,
                    token_env=config.container_worker_token_env,
                    timeout_seconds=config.container_worker_request_timeout_seconds,
                )
            try:
                status = client.codex_auth_profile_status(profile_id)
            except ContainerWorkerClientError as exc:
                if exc.status_code == 404:
                    raise ValueError(
                        f"Codex subscription profile {profile_id!r} no longer exists"
                    ) from exc
                raise
            if not bool(status.get("enabled")):
                raise ValueError(f"Codex subscription profile {profile_id!r} is disabled")
        repository.upsert_session(session)
