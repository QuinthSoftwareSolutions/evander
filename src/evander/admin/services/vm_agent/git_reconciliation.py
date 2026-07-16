from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import fcntl

from evander.admin.services.vm_agent.common import (
    Any,
    EffectProviderFactory,
    Mapping,
    Path,
    VmActionResult,
    hashlib,
    os,
)
from evander.events.provider_models import AgentCloneRepoEffectDefinition
from evander.git_ssh_authorized_keys import (
    remove_git_authorized_key_by_public_key_body,
    remove_git_authorized_keys_by_session_id,
)
from evander.provider_runtime.adapters import AdminRuntimeProviderAdapter


_RECONCILIATION_MARKER = "managed_git_remote_reconciliation_required"
_GIT_ACCESS_METADATA_KEYS = ("container_git_write_access", "managed_git_access")


class VmAgentManagedGitReconciliationMixin:
    """Reconcile migrated Git access before an agent can receive another prompt."""

    def _reconcile_managed_git_remote_before_dispatch(
        self,
        *,
        session_id: str,
        session: Any,
        backend: str,
        vm_name: str | None,
        vm_user: str,
    ) -> Any:
        metadata = self._session_metadata_mapping(session)
        if not bool(metadata.get(_RECONCILIATION_MARKER)):
            return session

        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            raise RuntimeError(
                "Managed Git remote reconciliation could not identify the session; "
                "agent dispatch was not started"
            )

        try:
            with self._managed_git_reconciliation_lock(normalized_session_id):
                current = self._session_repository().get_session(normalized_session_id)
                if current is None:
                    raise RuntimeError("session no longer exists")
                current_metadata = self._session_metadata_mapping(current)
                if not bool(current_metadata.get(_RECONCILIATION_MARKER)):
                    return current

                managed_repo_id, access_mode = self._managed_git_reconciliation_target(
                    current_metadata,
                    default_access_mode=(
                        "read-only" if backend == "container_worker_vm" else "write"
                    ),
                )
                self._revoke_migrated_git_access(
                    session_id=normalized_session_id,
                    metadata=current_metadata,
                )

                managed_git = self._deps.managed_git_admin
                if managed_git is None:
                    raise RuntimeError("managed Git service is not configured")
                managed_git.unbind_all("authorized_sessions", normalized_session_id)

                result = self._perform_managed_git_remote_reconciliation(
                    session=current,
                    session_id=normalized_session_id,
                    managed_repo_id=managed_repo_id,
                    access_mode=access_mode,
                    backend=backend,
                    vm_name=vm_name,
                    vm_user=vm_user,
                )
                if not self._managed_git_reconciliation_succeeded(result):
                    raise RuntimeError("managed Git setup did not complete")

                refreshed = self._session_repository().get_session(normalized_session_id)
                if refreshed is None:
                    raise RuntimeError("session no longer exists")
                refreshed_metadata = self._session_metadata_mapping(refreshed)
                if not self._has_reconciled_managed_git_access(
                    refreshed_metadata,
                    managed_repo_id=managed_repo_id,
                    access_mode=access_mode,
                    backend=backend,
                ):
                    raise RuntimeError("managed Git access verification failed")

                cleared_metadata = dict(refreshed_metadata)
                cleared_metadata.pop(_RECONCILIATION_MARKER, None)
                cleared = refreshed.model_copy(update={"metadata": cleared_metadata})
                self._persist_session(normalized_session_id, cleared)

                persisted = self._session_repository().get_session(normalized_session_id)
                if persisted is None or bool(
                    self._session_metadata_mapping(persisted).get(_RECONCILIATION_MARKER)
                ):
                    raise RuntimeError("could not persist reconciliation state")
                return persisted
        except Exception:
            # Reconciliation errors must not leak host repository or key paths through
            # an operator-facing prompt result. The durable marker is intentionally
            # left in place so a later wake retries the complete operation.
            self._retain_managed_git_reconciliation_marker(
                session_id=normalized_session_id,
                fallback_session=session,
            )
            raise RuntimeError(
                f"Managed Git remote reconciliation failed for session "
                f"{normalized_session_id}; agent dispatch was not started"
            ) from None

    def _retain_managed_git_reconciliation_marker(
        self,
        *,
        session_id: str,
        fallback_session: Any,
    ) -> None:
        try:
            current = self._session_repository().get_session(session_id) or fallback_session
            metadata = dict(self._session_metadata_mapping(current))
            if bool(metadata.get(_RECONCILIATION_MARKER)):
                return
            metadata[_RECONCILIATION_MARKER] = True
            self._persist_session(
                session_id,
                current.model_copy(update={"metadata": metadata}),
            )
        except Exception:
            # The original state already carries the marker. A repository write
            # failure cannot be repaired safely here and must still block dispatch.
            return

    def _perform_managed_git_remote_reconciliation(
        self,
        *,
        session: Any,
        session_id: str,
        managed_repo_id: str,
        access_mode: str,
        backend: str,
        vm_name: str | None,
        vm_user: str,
    ) -> object:
        directory_path = str(getattr(session, "directory_path", "") or "root")
        if backend == "libvirt_vm":
            normalized_vm_name = str(vm_name or getattr(session, "vm_id", "") or "").strip()
            if not normalized_vm_name:
                raise RuntimeError("managed Git reconciliation could not identify the VM")
            return self.clone_repo_into_vm(
                vm_name=normalized_vm_name,
                session_id=session_id,
                managed_repo_id=managed_repo_id,
                vm_user=vm_user,
                directory_path=directory_path,
            )

        if backend != "container_worker_vm":
            raise RuntimeError("managed Git reconciliation found an unsupported backend")
        provider_config = getattr(self.runtime, "provider_config", None)
        if provider_config is None:
            raise RuntimeError("provider configuration is unavailable")
        adapter = AdminRuntimeProviderAdapter(self)
        provider = EffectProviderFactory(
            provider_config_resolver=provider_config,
            admin_adapter=adapter,
        ).build(
            AgentCloneRepoEffectDefinition(
                id=f"managed-git-reconcile-{session_id}",
                display_name="Reconcile managed Git remote",
                type="effect:agent:clone-repo",
                session_id=session_id,
                managed_repo_id=managed_repo_id,
                vm_user=vm_user,
                git_access_mode=access_mode,
            ),
            directory_path=directory_path,
        )
        return provider.execute()

    @property
    def managed_git_admin(self) -> Any | None:
        """Expose managed Git only to the narrow provider-runtime adapter."""

        return self._deps.managed_git_admin

    def _revoke_migrated_git_access(
        self,
        *,
        session_id: str,
        metadata: Mapping[str, Any],
    ) -> None:
        paths: set[str] = set()
        exact_keys: set[tuple[str, str]] = set()
        for access in self._managed_git_access_records(metadata):
            path = str(access.get("authorized_keys_path") or "").strip()
            if path:
                paths.add(path)
                public_key_body = str(access.get("public_key_body") or "").strip()
                if public_key_body:
                    exact_keys.add((path, public_key_body))
        for path, public_key_body in sorted(exact_keys):
            remove_git_authorized_key_by_public_key_body(
                authorized_keys_path=path,
                public_key_body=public_key_body,
            )
        for path in sorted(paths):
            remove_git_authorized_keys_by_session_id(
                authorized_keys_path=path,
                session_id=session_id,
            )

    @classmethod
    def _managed_git_reconciliation_target(
        cls,
        metadata: Mapping[str, Any],
        *,
        default_access_mode: str,
    ) -> tuple[str, str]:
        access_records = list(cls._managed_git_access_records(metadata))
        access_repo_ids = {
            str(access.get("managed_repo_id") or "").strip()
            for access in access_records
            if str(access.get("managed_repo_id") or "").strip()
        }
        repo_ids = access_repo_ids or cls._managed_repo_ids_in_metadata(metadata)
        if len(repo_ids) != 1:
            raise RuntimeError("session does not identify exactly one managed repository")
        managed_repo_id = next(iter(repo_ids))

        modes = {
            cls._normalize_git_access_mode(access.get("access_mode"))
            for access in access_records
            if str(access.get("managed_repo_id") or "").strip() == managed_repo_id
        }
        if len(modes) > 1:
            raise RuntimeError("session has conflicting managed Git access modes")
        return managed_repo_id, next(iter(modes), default_access_mode)

    @classmethod
    def _managed_git_access_records(
        cls,
        value: object,
    ) -> Iterator[Mapping[str, Any]]:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if key in _GIT_ACCESS_METADATA_KEYS and isinstance(item, Mapping):
                    yield item
                yield from cls._managed_git_access_records(item)
        elif isinstance(value, list):
            for item in value:
                yield from cls._managed_git_access_records(item)

    @classmethod
    def _managed_repo_ids_in_metadata(cls, value: object) -> set[str]:
        result: set[str] = set()
        if isinstance(value, Mapping):
            for key, item in value.items():
                if key == "managed_repo_id":
                    normalized = str(item or "").strip()
                    if normalized:
                        result.add(normalized)
                else:
                    result.update(cls._managed_repo_ids_in_metadata(item))
        elif isinstance(value, list):
            for item in value:
                result.update(cls._managed_repo_ids_in_metadata(item))
        return result

    @staticmethod
    def _normalize_git_access_mode(value: object) -> str:
        normalized = str(value or "write").strip().casefold().replace("_", "-")
        if normalized in {"", "write", "writable"}:
            return "write"
        if normalized in {"read", "readonly", "read-only"}:
            return "read-only"
        raise RuntimeError("session has an invalid managed Git access mode")

    @staticmethod
    def _session_metadata_mapping(session: Any) -> Mapping[str, Any]:
        metadata = getattr(session, "metadata", {})
        return metadata if isinstance(metadata, Mapping) else {}

    @staticmethod
    def _managed_git_reconciliation_succeeded(result: object) -> bool:
        if isinstance(result, VmActionResult):
            return bool(result.success)
        if isinstance(result, Mapping):
            return bool(result.get("success"))
        return bool(getattr(result, "success", False))

    @classmethod
    def _has_reconciled_managed_git_access(
        cls,
        metadata: Mapping[str, Any],
        *,
        managed_repo_id: str,
        access_mode: str,
        backend: str,
    ) -> bool:
        expected_key = (
            "container_git_write_access"
            if backend == "container_worker_vm"
            else "managed_git_access"
        )
        access = metadata.get(expected_key)
        if not isinstance(access, Mapping):
            return False
        if str(access.get("managed_repo_id") or "").strip() != managed_repo_id:
            return False
        if cls._normalize_git_access_mode(access.get("access_mode")) != access_mode:
            return False
        return bool(
            str(access.get("public_key_body") or "").strip()
            and str(access.get("authorized_keys_path") or "").strip()
        )

    @contextmanager
    def _managed_git_reconciliation_lock(self, session_id: str) -> Iterator[None]:
        lock_root = Path(self.config.runtime_dir) / "managed-git-reconciliation-locks"
        lock_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(lock_root, 0o700)
        except OSError:
            pass
        digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
        descriptor = os.open(lock_root / f"{digest}.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
