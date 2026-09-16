from __future__ import annotations

from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import logging
from pathlib import Path
import re
from typing import Any

from evander.events.provider_models import AgentPrepareReferenceRepositoriesEffectDefinition
from evander.execution.container_worker_client import ContainerWorkerClientError
from evander.managed_git_grants import managed_git_grants_from_metadata

from .agent_clone_repo import (
    AgentCloneRepoEffectProvider,
    _container_git_connection_config,
    _container_git_remote_url_with_ssh_alias,
    _remote_url_requires_git_ssh,
)
from .agent_session import (
    as_bool_or_none,
    container_backend,
    effective_config_for_session,
    require_container_session_id,
    required_string,
    selected_execution_backend_for_target,
)
from .base import EffectProviderBase, scoped_effect_directory_path


_REFERENCE_REPO_CONFIG_VERSION = "1"
_MAX_PARALLEL_REPOSITORIES = 4
_OBJECT_ID_PATTERN = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _ResolvedReferenceRepository:
    index: int
    repo_source: str
    managed_repo_id: str
    repo_name: str
    source_ref: str
    commit_sha: str | None
    clone_url: str
    probe_performed: bool
    probe_reused: bool
    refresh_performed: bool


@dataclass(frozen=True)
class _PreparedReferenceRepository:
    record: Any
    trust_reconciled: bool


def _reference_preparation_fingerprint(
    *,
    repository: _ResolvedReferenceRepository,
    clone_source: str,
) -> str | None:
    if repository.commit_sha is None:
        return None
    payload = {
        "version": 1,
        "managed_repo_id": repository.managed_repo_id,
        "repo_name": repository.repo_name,
        "repo_source": clone_source,
        "source_ref": repository.source_ref,
        "commit_sha": repository.commit_sha,
        "configuration_version": _REFERENCE_REPO_CONFIG_VERSION,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _prepare_reference_repo_on_backend(
    *,
    backend: Any,
    kwargs: dict[str, Any],
    preparation_fingerprint: str | None,
) -> Any:
    try:
        return backend.prepare_reference_repo(
            **kwargs,
            preparation_fingerprint=preparation_fingerprint,
        )
    except TypeError as exc:
        if "preparation_fingerprint" not in str(exc):
            raise
        return backend.prepare_reference_repo(**kwargs)


class AgentPrepareReferenceRepositoriesEffectProvider(EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        effect = self._effect()
        payload = {
            "target": effect.target,
            "session_id": effect.session_id,
            "vm_name": effect.vm_name,
            "directory_path": effect.directory_path,
            "repositories": effect.repositories,
            "refresh_upstream": effect.refresh_upstream,
        }
        if resolved_config:
            payload.update(dict(resolved_config))
        context = self._context()
        directory_path = scoped_effect_directory_path(context, payload.get("directory_path"))
        execution_backend, config = selected_execution_backend_for_target(
            context,
            payload,
            directory_path=directory_path,
        )
        if execution_backend != "container_worker_vm":
            raise ValueError(
                "effect:agent:prepare-reference-repositories currently requires "
                "execution_backend=container_worker_vm"
            )
        session_id, session = require_container_session_id(
            context,
            payload,
            effect_type="effect:agent:prepare-reference-repositories",
            config=config,
        )
        session_directory_path = str(getattr(session, "directory_path", None) or directory_path)
        config = effective_config_for_session(
            context,
            session_id,
            fallback_directory_path=session_directory_path,
        )
        repositories = _normalize_repository_inputs(payload.get("repositories"))
        refresh_upstream = as_bool_or_none(payload.get("refresh_upstream")) or False
        requested_at = datetime.now(UTC)
        resolved = self._resolve_repositories(
            repositories,
            directory_path=session_directory_path,
            session_id=session_id,
            requested_at=requested_at,
            refresh_upstream=refresh_upstream,
        )
        _validate_resolved_repositories(resolved)

        backend = container_backend(context, config)
        backend.prepare_session(
            session_id=session_id,
            metadata={
                "execution_backend": "container_worker_vm",
                "directory_path": session_directory_path,
            },
        )
        grants = managed_git_grants_from_metadata(getattr(session, "metadata", {}))
        prepared_inputs: list[tuple[_ResolvedReferenceRepository, str, bool]] = []
        access_provider = AgentCloneRepoEffectProvider()
        access_provider.runtime_context = context
        for repository in resolved:
            clone_source = repository.clone_url
            access_reused = False
            if _remote_url_requires_git_ssh(repository.clone_url):
                access_metadata = grants.get(repository.managed_repo_id)
                if _usable_read_grant(access_metadata):
                    access_reused = True
                else:
                    if _writable_grant(access_metadata):
                        raise RuntimeError(
                            "existing writable managed Git access is not usable; "
                            "reference repository preparation will not replace it"
                        )
                    managed_git = getattr(context.admin_service, "managed_git_admin", None)
                    resolve_internal = getattr(managed_git, "resolve_internal_repository", None)
                    if not callable(resolve_internal):
                        raise RuntimeError(
                            "managed Git internal repository resolver is unavailable"
                        )
                    bare_repo_path, managed_git_root = resolve_internal(
                        repository.managed_repo_id,
                        session_directory_path,
                    )
                    access_metadata = access_provider._provision_container_git_access(
                        backend=backend,
                        config=config,
                        context=context,
                        session_id=session_id,
                        issue_setup=_container_git_connection_config(
                            context,
                            session_directory_path,
                        ),
                        managed_repo_id=repository.managed_repo_id,
                        bare_repo_path=bare_repo_path,
                        managed_git_root=managed_git_root,
                        access_mode="read-only",
                        lifetime="session",
                    )
                    grants[repository.managed_repo_id] = access_metadata
                clone_source = _container_git_remote_url_with_ssh_alias(
                    repository.clone_url,
                    required_string(
                        access_metadata.get("git_ssh_alias") if access_metadata else None,
                        "managed Git read grant is missing git_ssh_alias",
                    ),
                )
            prepared_inputs.append((repository, clone_source, access_reused))

        results: dict[int, dict[str, Any]] = {}
        failures: list[dict[str, Any]] = []
        with ThreadPoolExecutor(
            max_workers=min(_MAX_PARALLEL_REPOSITORIES, len(prepared_inputs))
        ) as executor:
            futures = {
                executor.submit(
                    self._prepare_reference_repository,
                    backend=backend,
                    session_id=session_id,
                    directory_path=session_directory_path,
                    repository=repository,
                    clone_source=clone_source,
                    refresh_upstream=refresh_upstream,
                    preparation_fingerprint=(
                        None
                        if refresh_upstream
                        else _reference_preparation_fingerprint(
                            repository=repository,
                            clone_source=clone_source,
                        )
                    ),
                ): (repository, access_reused)
                for repository, clone_source, access_reused in prepared_inputs
            }
            for future in as_completed(futures):
                repository, access_reused = futures[future]
                try:
                    prepared = future.result()
                except Exception as exc:  # noqa: BLE001
                    failures.append(
                        {
                            "index": repository.index,
                            "repo_source": repository.repo_source,
                            "managed_repo_id": repository.managed_repo_id,
                            "error": str(exc),
                        }
                    )
                    continue
                record = prepared.record
                resolved_commit_sha = required_string(
                    getattr(record, "commit_sha", None),
                    f"worker did not return a commit for {repository.repo_name}",
                )
                result = {
                    "repo_source": repository.repo_source,
                    "managed_repo_id": repository.managed_repo_id,
                    "repo_name": repository.repo_name,
                    "source_ref": repository.source_ref,
                    "commit_sha": resolved_commit_sha,
                    "target_dir": record.target_dir,
                    "probe_performed": repository.probe_performed,
                    "probe_reused": repository.probe_reused,
                    "refresh_performed": repository.refresh_performed,
                    "checkout_updated": bool(record.cloned or record.checkout_updated),
                    "configuration_updated": bool(record.configuration_updated),
                    "access_reused": access_reused,
                    "trust_reconciled": prepared.trust_reconciled,
                    "preparation_reused": bool(getattr(record, "preparation_reused", False)),
                }
                logger.info(
                    "metric_agent_prepare_reference_repository "
                    "session_id=%s managed_repo_id=%s branch=%s "
                    "probe_performed=%s probe_reused=%s refresh_performed=%s "
                    "checkout_updated=%s trust_reconciled=%s",
                    session_id,
                    repository.managed_repo_id,
                    repository.source_ref.removeprefix("refs/heads/"),
                    repository.probe_performed,
                    repository.probe_reused,
                    repository.refresh_performed,
                    result["checkout_updated"],
                    result["trust_reconciled"],
                )
                results[repository.index] = result

        bind_repository = getattr(context.admin_service, "bind_managed_git_repository", None)
        if callable(bind_repository):
            for repository in resolved:
                if repository.index in results:
                    bind_repository(
                        managed_repo_id=repository.managed_repo_id,
                        kind="sessions",
                        binding_id=session_id,
                    )
        ordered_results = [results[index] for index in sorted(results)]
        if failures:
            raise RuntimeError(
                "Reference repository preparation failed: "
                + json.dumps(
                    {
                        "completed": ordered_results,
                        "failures": sorted(failures, key=lambda item: item["index"]),
                    },
                    sort_keys=True,
                )
            )
        return {
            "success": True,
            "session_id": session_id,
            "repositories": ordered_results,
            "preparation_reused": bool(ordered_results)
            and all(item["preparation_reused"] for item in ordered_results),
        }

    def _prepare_reference_repository(
        self,
        *,
        backend: Any,
        session_id: str,
        directory_path: str,
        repository: _ResolvedReferenceRepository,
        clone_source: str,
        refresh_upstream: bool,
        preparation_fingerprint: str | None,
    ) -> _PreparedReferenceRepository:
        try:
            record = _prepare_reference_repo_on_backend(
                backend=backend,
                kwargs={
                    "session_id": session_id,
                    "repo_source": clone_source,
                    "repo_name": repository.repo_name,
                    "source_ref": repository.source_ref,
                    "commit_sha": repository.commit_sha,
                    "configuration_version": _REFERENCE_REPO_CONFIG_VERSION,
                    "update_existing": refresh_upstream,
                },
                preparation_fingerprint=preparation_fingerprint,
            )
        except ContainerWorkerClientError as exc:
            transition = (
                None
                if refresh_upstream
                else _reference_repo_trust_transition_from_error(
                    exc,
                    expected_repo_name=repository.repo_name,
                )
            )
            if transition is None or repository.commit_sha is None:
                raise
            trusted_commit, current_commit = transition
            verify_transition = getattr(
                self._context().admin_service,
                "verify_managed_git_reference_repo_commit_transition",
                None,
            )
            if not callable(verify_transition):
                raise RuntimeError(
                    "managed Git reference-repository trust verification is unavailable"
                ) from exc
            try:
                verification = _mapping_result(
                    verify_transition(
                        repository.managed_repo_id,
                        directory_path=directory_path,
                        branch=repository.source_ref.removeprefix("refs/heads/"),
                        cached_branch_commit_sha=repository.commit_sha,
                        trusted_commit_sha=trusted_commit,
                        current_commit_sha=current_commit,
                    )
                )
            except Exception as verification_error:
                raise RuntimeError(
                    f"reference repository {repository.repo_name} trust transition was "
                    f"rejected: {verification_error}"
                ) from verification_error
            if verification.get("current_commit_sha") != current_commit:
                raise RuntimeError(
                    "managed Git reference-repository trust verification returned an "
                    "unexpected commit"
                )
            record = _prepare_reference_repo_on_backend(
                backend=backend,
                kwargs={
                    "session_id": session_id,
                    "repo_source": clone_source,
                    "repo_name": repository.repo_name,
                    "source_ref": repository.source_ref,
                    "commit_sha": current_commit,
                    "configuration_version": _REFERENCE_REPO_CONFIG_VERSION,
                    "update_existing": True,
                },
                preparation_fingerprint=None,
            )
            return _PreparedReferenceRepository(record=record, trust_reconciled=True)
        return _PreparedReferenceRepository(record=record, trust_reconciled=False)

    def _resolve_repositories(
        self,
        repositories: list[dict[str, str | None]],
        *,
        directory_path: str,
        session_id: str,
        requested_at: datetime,
        refresh_upstream: bool,
    ) -> list[_ResolvedReferenceRepository]:
        with ThreadPoolExecutor(
            max_workers=min(_MAX_PARALLEL_REPOSITORIES, len(repositories))
        ) as executor:
            return list(
                executor.map(
                    lambda indexed: self._resolve_repository(
                        indexed[0],
                        indexed[1],
                        directory_path=directory_path,
                        session_id=session_id,
                        requested_at=requested_at,
                        refresh_upstream=refresh_upstream,
                    ),
                    enumerate(repositories),
                )
            )

    def _resolve_repository(
        self,
        index: int,
        repository: dict[str, str | None],
        *,
        directory_path: str,
        session_id: str,
        requested_at: datetime,
        refresh_upstream: bool,
    ) -> _ResolvedReferenceRepository:
        admin_service = self._context().admin_service
        get_repository = getattr(admin_service, "get_managed_git_repository", None)
        refresh_branch = getattr(
            admin_service,
            "refresh_managed_git_repository_if_branch_changed",
            None,
        )
        resolve_branch = getattr(admin_service, "resolve_managed_git_branch_commit", None)
        clone_resolver = getattr(admin_service, "resolve_managed_git_clone", None)
        managed_repo_id = required_string(
            repository.get("managed_repo_id"),
            "managed_repo_id is required",
        )
        required_operations = (get_repository, resolve_branch, clone_resolver)
        if not all(callable(method) for method in required_operations) or (
            refresh_upstream and not callable(refresh_branch)
        ):
            raise RuntimeError("managed Git reference-repository operations are unavailable")
        registered = _mapping_result(
            get_repository(managed_repo_id, directory_path=directory_path)
        )
        managed_repo_id = required_string(
            registered.get("managed_repo_id"),
            "managed Git lookup did not return managed_repo_id",
        )
        repo_source = required_string(
            registered.get("canonical_repository_url"),
            "managed Git repository does not have a canonical repository URL",
        )
        verification: dict[str, Any] = {}
        if refresh_upstream:
            verification = _mapping_result(
                refresh_branch(
                    managed_repo_id,
                    directory_path=directory_path,
                    branch=repository.get("branch"),
                    reuse_if_verified_after=requested_at,
                )
            )
        branch = required_string(
            verification.get("branch")
            or repository.get("branch")
            or registered.get("default_branch"),
            f"managed repository {managed_repo_id} has no default branch",
        )
        commit_sha = resolve_branch(
            managed_repo_id,
            directory_path=directory_path,
            branch=branch,
        )
        if refresh_upstream and not commit_sha:
            raise ValueError(
                f"managed repository {managed_repo_id} has no cached commit for branch "
                f"{branch} after refresh"
            )
        if commit_sha:
            commit_sha = required_string(commit_sha, "managed Git branch commit is unavailable")
        clone_info = _mapping_result(
            clone_resolver(
                managed_repo_id=managed_repo_id,
                directory_path=directory_path,
                session_id=session_id,
            )
        )
        return _ResolvedReferenceRepository(
            index=index,
            repo_source=repo_source,
            managed_repo_id=managed_repo_id,
            repo_name=required_string(
                clone_info.get("repo_name")
                or clone_info.get("display_name")
                or managed_repo_id,
                "managed Git display name is unavailable",
            ),
            source_ref=f"refs/heads/{branch}",
            commit_sha=commit_sha,
            clone_url=required_string(
                clone_info.get("clone_url") or clone_info.get("worker_repo_source"),
                "managed Git clone URL is unavailable",
            ),
            probe_performed=bool(verification.get("probe_performed")),
            probe_reused=bool(verification.get("probe_reused")),
            refresh_performed=bool(verification.get("refresh_performed")),
        )

    def _effect(self) -> AgentPrepareReferenceRepositoriesEffectDefinition:
        if not isinstance(
            self.effect_definition,
            AgentPrepareReferenceRepositoriesEffectDefinition,
        ):
            raise RuntimeError(
                "AgentPrepareReferenceRepositoriesEffectProvider initialized with an invalid "
                "effect definition"
            )
        return self.effect_definition

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError(
                "AgentPrepareReferenceRepositoriesEffectProvider has no runtime context"
            )
        return self.runtime_context


def _normalize_repository_inputs(value: object) -> list[dict[str, str | None]]:
    if not isinstance(value, list) or not value:
        raise ValueError(
            "effect:agent:prepare-reference-repositories requires a non-empty repositories array"
        )
    result: list[dict[str, str | None]] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ValueError(f"repositories[{index}] must be an object")
        if "repo_source" in item:
            raise ValueError(
                f"repositories[{index}].repo_source is not supported; select a managed_repo_id"
            )
        raw_managed_repo_id = item.get("managed_repo_id")
        if not isinstance(raw_managed_repo_id, str) or not raw_managed_repo_id.strip():
            raise ValueError(f"repositories[{index}].managed_repo_id is required")
        managed_repo_id = raw_managed_repo_id.strip()
        if managed_repo_id in seen:
            raise ValueError(
                f"repositories contains duplicate managed repository: {managed_repo_id}"
            )
        seen.add(managed_repo_id)
        branch = str(item.get("branch") or "").strip() or None
        result.append(
            {
                "managed_repo_id": managed_repo_id,
                "branch": branch,
            }
        )
    return result


def _mapping_result(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="python")
        if isinstance(dumped, Mapping):
            return dict(dumped)
    raise RuntimeError("managed Git operation returned an invalid response")


def _reference_repo_trust_transition_from_error(
    error: ContainerWorkerClientError,
    *,
    expected_repo_name: str,
) -> tuple[str, str] | None:
    if error.status_code != 409 or not isinstance(error.payload, Mapping):
        return None
    details = error.payload.get("reference_repo_trust_conflict")
    if not isinstance(details, Mapping):
        return None
    if details.get("code") != "reference_repo_trust_mismatch":
        return None
    if details.get("repo_name") != expected_repo_name:
        return None
    trusted_commit = str(details.get("trusted_commit") or "").strip().casefold()
    current_commit = str(details.get("current_commit") or "").strip().casefold()
    if (
        _OBJECT_ID_PATTERN.fullmatch(trusted_commit) is None
        or _OBJECT_ID_PATTERN.fullmatch(current_commit) is None
        or trusted_commit == current_commit
    ):
        return None
    return trusted_commit, current_commit


def _validate_resolved_repositories(
    repositories: list[_ResolvedReferenceRepository],
) -> None:
    managed_repo_ids: set[str] = set()
    repo_names: set[str] = set()
    for repository in repositories:
        if repository.managed_repo_id in managed_repo_ids:
            raise ValueError(
                "repositories resolve to the same managed repository more than once: "
                f"{repository.managed_repo_id}"
            )
        managed_repo_ids.add(repository.managed_repo_id)
        if repository.repo_name in repo_names:
            raise ValueError(
                "repositories resolve to the same worker checkout name more than once: "
                f"{repository.repo_name}"
            )
        repo_names.add(repository.repo_name)


def _usable_read_grant(grant: Mapping[str, Any] | None) -> bool:
    if not isinstance(grant, Mapping):
        return False
    if str(grant.get("access_mode") or "").strip().casefold().replace("_", "-") not in {
        "read",
        "readonly",
        "read-only",
        "write",
        "writable",
    }:
        return False
    if str(grant.get("lifetime") or "").strip() != "session":
        return False
    alias = str(grant.get("git_ssh_alias") or "").strip()
    public_key_body = str(grant.get("public_key_body") or "").strip()
    authorized_keys_path = str(grant.get("authorized_keys_path") or "").strip()
    if not alias or not public_key_body or not authorized_keys_path:
        return False
    try:
        return public_key_body in Path(authorized_keys_path).read_text(encoding="utf-8")
    except OSError:
        return False


def _writable_grant(grant: Mapping[str, Any] | None) -> bool:
    if not isinstance(grant, Mapping):
        return False
    return str(grant.get("access_mode") or "").strip().casefold().replace("_", "-") in {
        "write",
        "writable",
    }
