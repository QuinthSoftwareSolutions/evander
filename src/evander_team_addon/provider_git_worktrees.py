"""Prepare task worktrees and validate backend preparation receipts.

Select the execution backend once, delegate container preparation or build the
libvirt command, then check its immutable base and provenance receipt. Shell
command construction is isolated in provider_git_worktree_scripts."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import (
    Mapping,
    Sequence,
)
from typing import (
    Any,
)

from evander.effects.providers.agent_exec import (
    AgentExecEffectProvider,
)
from evander.effects.providers.agent_session import (
    container_backend,
    selected_execution_backend_for_target,
)
from evander.events.provider_models import (
    AgentExecEffectDefinition,
)
from evander.execution.container_worker_client import (
    ContainerWorkerClientError,
)
from evander.git_identity import (
    DEFAULT_AGENT_GIT_IDENTITY,
    GitIdentity,
)

from .provider_git_errors import (
    DependencyGitPreparationError,
)
from .provider_git_metadata import (
    _project_repo_task_worktree_name,
)
from .provider_git_worktree_scripts import _build_vm_task_worktree_command
from .provider_values import (
    _optional_text,
    _required_text,
)


def _prepare_project_repo_task_worktree(
    context: Any,
    *,
    project_repo: Mapping[str, Any],
    session_id: str,
    task_id: str,
    agent_branch: str,
    base_ref: str,
    worktree_name: str | None = None,
    preparation_attempt_id: str | None = None,
    worktree_identity_commit: str | None = None,
    preparation_generation: str | None = None,
    prior_base_commit: str | None = None,
    pinned_base_commit: str | None = None,
    pinned_base_tree: str | None = None,
    dependency_git_sources: Sequence[Mapping[str, str]] = (),
    dependency_composition_mode: str = "automatic",
    dependency_composition_recovery_id: str | None = None,
    resolved_dependency_branch: str | None = None,
    resolved_dependency_commit_sha: str | None = None,
    task_author_identity: GitIdentity | None = None,
    dependency_merge_author_identity: GitIdentity = DEFAULT_AGENT_GIT_IDENTITY,
    composition_result: dict[str, Any] | None = None,
    preparation_receipt_result: dict[str, Any] | None = None,
) -> tuple[str, str]:
    repo_name = _required_text(project_repo.get("guest_repo_name"), "guest_repo_name")
    task_hash = hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:8]
    identity_commit = worktree_identity_commit or pinned_base_commit
    resolved_worktree_name = worktree_name or _project_repo_task_worktree_name(
        task_id=task_id,
        preparation_generation=preparation_generation,
        identity_commit=identity_commit,
    )
    directory_path = str(getattr(context, "metadata", {}).get("directory_path") or "root")
    selected_backend, config = selected_execution_backend_for_target(
        context,
        {"session_id": session_id},
        directory_path=directory_path,
    )
    execution_backend = _optional_text(project_repo.get("execution_backend")) or selected_backend
    if execution_backend == "container_worker_vm":
        return _prepare_container_task_worktree(
            agent_branch=agent_branch,
            base_ref=base_ref,
            composition_result=composition_result,
            config=config,
            context=context,
            dependency_composition_mode=dependency_composition_mode,
            dependency_composition_recovery_id=dependency_composition_recovery_id,
            dependency_git_sources=dependency_git_sources,
            dependency_merge_author_identity=dependency_merge_author_identity,
            pinned_base_commit=pinned_base_commit,
            pinned_base_tree=pinned_base_tree,
            preparation_attempt_id=preparation_attempt_id,
            preparation_receipt_result=preparation_receipt_result,
            prior_base_commit=prior_base_commit,
            repo_name=repo_name,
            resolved_dependency_branch=resolved_dependency_branch,
            resolved_dependency_commit_sha=resolved_dependency_commit_sha,
            resolved_worktree_name=resolved_worktree_name,
            session_id=session_id,
            task_author_identity=task_author_identity,
            task_id=task_id,
        )
    if execution_backend != "libvirt_vm":
        raise RuntimeError(
            f"Team task worktrees do not support execution backend {execution_backend!r}"
        )

    command, clone_dir, worktree_path = _build_vm_task_worktree_command(
        agent_branch=agent_branch,
        base_ref=base_ref,
        dependency_composition_mode=dependency_composition_mode,
        dependency_composition_recovery_id=dependency_composition_recovery_id,
        dependency_git_sources=dependency_git_sources,
        dependency_merge_author_identity=dependency_merge_author_identity,
        pinned_base_commit=pinned_base_commit,
        pinned_base_tree=pinned_base_tree,
        prior_base_commit=prior_base_commit,
        project_repo=project_repo,
        repo_name=repo_name,
        resolved_dependency_branch=resolved_dependency_branch,
        resolved_dependency_commit_sha=resolved_dependency_commit_sha,
        resolved_worktree_name=resolved_worktree_name,
        task_author_identity=task_author_identity,
        task_id=task_id,
    )
    provider = AgentExecEffectProvider()
    provider.initialize(
        AgentExecEffectDefinition(
            id=f"team-prepare-task-worktree-{task_hash}",
            display_name="Prepare Team task worktree",
            type="effect:agent:exec",
            session_id=session_id,
            command=command,
            working_directory=clone_dir,
            timeout_seconds=120,
            check=False,
        ),
        context,
    )
    result = provider.execute()
    return _read_vm_task_worktree_result(
        agent_branch=agent_branch,
        base_ref=base_ref,
        composition_result=composition_result,
        dependency_git_sources=dependency_git_sources,
        pinned_base_commit=pinned_base_commit,
        preparation_attempt_id=preparation_attempt_id,
        preparation_receipt_result=preparation_receipt_result,
        result=result,
        task_id=task_id,
        worktree_path=worktree_path,
    )


def _prepare_container_task_worktree(
    *,
    agent_branch: str,
    base_ref: str,
    composition_result: dict[str, Any] | None,
    config: Any,
    context: Any,
    dependency_composition_mode: str,
    dependency_composition_recovery_id: str | None,
    dependency_git_sources: Sequence[Mapping[str, str]],
    dependency_merge_author_identity: GitIdentity,
    pinned_base_commit: str | None,
    pinned_base_tree: str | None,
    preparation_attempt_id: str | None,
    preparation_receipt_result: dict[str, Any] | None,
    prior_base_commit: str | None,
    repo_name: str,
    resolved_dependency_branch: str | None,
    resolved_dependency_commit_sha: str | None,
    resolved_worktree_name: str,
    session_id: str,
    task_author_identity: GitIdentity | None,
    task_id: str,
) -> tuple[str, str]:
    expected_base_commit = (
        pinned_base_commit
        if dependency_composition_mode == "adopt_resolved" and preparation_attempt_id is not None
        else prior_base_commit or pinned_base_commit
    )
    # A composed retained base has a different tree from its pinned root.
    # The worker validates that root through its composition record below.
    expected_base_tree = (
        pinned_base_tree
        if pinned_base_commit is not None
        and expected_base_commit is not None
        and expected_base_commit.casefold() == pinned_base_commit.casefold()
        else None
    )
    dependency_composition_kwargs = (
        {
            "dependency_composition_commit_message": (
                f"Compose accepted dependencies for task {task_id}"
            )
        }
        if dependency_git_sources
        else {}
    )
    try:
        record = container_backend(context, config).prepare_task_worktree(
            session_id=session_id,
            repo_name=repo_name,
            worktree_name=resolved_worktree_name,
            branch=agent_branch,
            base_ref=base_ref,
            expected_base_commit_sha=expected_base_commit,
            expected_base_tree_hash=expected_base_tree,
            dependency_git_sources=[dict(row) for row in dependency_git_sources],
            dependency_composition_mode=dependency_composition_mode,
            dependency_composition_recovery_id=(dependency_composition_recovery_id),
            resolved_dependency_branch=resolved_dependency_branch,
            resolved_dependency_commit_sha=resolved_dependency_commit_sha,
            dependency_merge_author_name=dependency_merge_author_identity.name,
            dependency_merge_author_email=dependency_merge_author_identity.email,
            **(
                {"preparation_attempt_id": preparation_attempt_id}
                if preparation_attempt_id is not None
                else {}
            ),
            **dependency_composition_kwargs,
            **(
                {
                    "git_author_name": task_author_identity.name,
                    "git_author_email": task_author_identity.email,
                }
                if task_author_identity is not None
                else {}
            ),
        )
    except ContainerWorkerClientError as exc:
        payload = exc.payload or {}
        conflict = payload.get("dependency_composition_conflict")
        raise DependencyGitPreparationError(
            task_id=task_id,
            message=str(exc),
            conflict=conflict if isinstance(conflict, Mapping) else None,
        ) from None
    except Exception as exc:
        if dependency_git_sources or pinned_base_commit is not None:
            raise DependencyGitPreparationError(
                task_id=task_id,
                message=str(exc),
            ) from None
        raise
    if record.branch != agent_branch:
        raise RuntimeError(
            f"prepared worktree {record.target_dir} uses {record.branch!r}, "
            f"expected {agent_branch!r}"
        )
    record_created = bool(getattr(record, "created", False))
    receipt_reused = bool(getattr(record, "preparation_receipt_reused", False))
    receipt_matches = (
        preparation_attempt_id is not None
        and getattr(record, "preparation_attempt_id", None) == preparation_attempt_id
        and receipt_reused
        and bool(getattr(record, "verified_clean", False))
    )
    if (
        not record_created
        and prior_base_commit is None
        and pinned_base_commit is None
        and not receipt_matches
    ):
        raise RuntimeError(
            "reused task worktree has no authoritative server-recorded base; "
            f"preserve {record.target_dir} and use an audited provenance recovery"
        )
    base_commit_sha = _optional_text(getattr(record, "base_commit_sha", None))
    if base_commit_sha is None and record_created:
        base_commit_sha = _optional_text(record.commit_sha)
    if base_commit_sha is None:
        base_commit_sha = _optional_text(pinned_base_commit or prior_base_commit)
    if base_commit_sha is None:
        error = (
            "reused task worktree did not report its base commit and no "
            "authoritative prior Git work context can recover it; preserve "
            f"{record.target_dir} and repair evander.baseCommit before retrying"
        )
        if dependency_git_sources or pinned_base_commit is not None:
            raise DependencyGitPreparationError(
                task_id=task_id,
                message=error,
            )
        raise RuntimeError(error)
    if pinned_base_commit is not None and dependency_composition_mode != "adopt_resolved":
        if dependency_git_sources:
            composition_root_commit = _optional_text(
                getattr(record, "composition_root_commit_sha", None)
            )
            if (
                composition_root_commit is None
                or composition_root_commit.casefold() != pinned_base_commit.casefold()
            ):
                raise DependencyGitPreparationError(
                    task_id=task_id,
                    message=(
                        f"prepared worktree {record.target_dir} reports composition root "
                        f"{composition_root_commit or 'none'}, not authoritative pinned "
                        f"composition root {pinned_base_commit}"
                    ),
                )
        elif base_commit_sha.casefold() != pinned_base_commit.casefold():
            raise DependencyGitPreparationError(
                task_id=task_id,
                message=(
                    f"prepared worktree {record.target_dir} reports base "
                    f"{base_commit_sha}, not authoritative pinned base commit "
                    f"{pinned_base_commit}"
                ),
            )
    if (
        dependency_composition_mode == "adopt_resolved"
        and resolved_dependency_commit_sha is not None
        and base_commit_sha.casefold() != resolved_dependency_commit_sha.casefold()
    ):
        raise DependencyGitPreparationError(
            task_id=task_id,
            message=(
                f"prepared worktree {record.target_dir} did not adopt resolved "
                f"dependency commit {resolved_dependency_commit_sha}"
            ),
        )
    if composition_result is not None:
        for field_name, record_field_name in (
            ("root_commit_sha", "composition_root_commit_sha"),
            ("composed_commit_sha", "composed_commit_sha"),
            ("composed_tree_hash", "composed_tree_hash"),
        ):
            value = _optional_text(getattr(record, record_field_name, None))
            if value is not None:
                composition_result[field_name] = value.casefold()
    if preparation_receipt_result is not None and preparation_attempt_id is not None:
        receipt_attempt_id = _optional_text(getattr(record, "preparation_attempt_id", None))
        receipt_fingerprint = _optional_text(
            getattr(record, "preparation_request_fingerprint", None)
        )
        if receipt_attempt_id is not None or receipt_fingerprint is not None:
            if receipt_attempt_id != preparation_attempt_id:
                raise RuntimeError(
                    "container worker returned a preparation receipt for a different attempt"
                )
            request_fingerprint = _required_text(
                receipt_fingerprint,
                "preparation request fingerprint",
            )
        else:
            request_fingerprint = (
                "legacy-container-worker:"
                + hashlib.sha256(
                    json.dumps(
                        {
                            "attempt_id": preparation_attempt_id,
                            "target_dir": record.target_dir,
                            "branch": record.branch,
                            "base_commit_sha": base_commit_sha,
                            "head_commit_sha": record.commit_sha,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
            )
        preparation_receipt_result.update(
            {
                "request_fingerprint": request_fingerprint,
                "head_commit_sha": _required_text(
                    getattr(record, "commit_sha", None),
                    "prepared head commit",
                ).casefold(),
                "receipt_reused": receipt_reused
                or (receipt_attempt_id is None and not record_created),
                "verified_clean": bool(getattr(record, "verified_clean", False)),
            }
        )
    return record.target_dir, base_commit_sha


def _read_vm_task_worktree_result(
    *,
    agent_branch: str,
    base_ref: str,
    composition_result: dict[str, Any] | None,
    dependency_git_sources: Sequence[Mapping[str, str]],
    pinned_base_commit: str | None,
    preparation_attempt_id: str | None,
    preparation_receipt_result: dict[str, Any] | None,
    result: Mapping[str, Any],
    task_id: str,
    worktree_path: str,
) -> tuple[str, str]:
    if not bool(result.get("success")):
        failure_evidence = "\n".join(
            str(result.get(field_name) or "")
            for field_name in ("error", "message", "stdout", "stderr")
        )
        error = (
            _optional_text(result.get("error"))
            or _optional_text(result.get("message"))
            or f"failed to prepare task worktree {worktree_path}"
        )
        if dependency_git_sources or pinned_base_commit is not None:
            conflict: dict[str, object] | None = None
            failure_lines = failure_evidence.splitlines()
            if any(line.strip() == "EVANDER_DEPENDENCY_CONFLICT=1" for line in failure_lines):
                conflict_values: dict[str, str] = {}
                conflict_paths: list[str] = []
                for line in failure_lines:
                    if line.startswith("EVANDER_DEPENDENCY_CONFLICT_PATH="):
                        path = line.partition("=")[2].strip()
                        if path and path not in conflict_paths:
                            conflict_paths.append(path)
                    elif line.startswith("EVANDER_DEPENDENCY_CONFLICT_") and "=" in line:
                        key, _separator, value = line.partition("=")
                        conflict_values[key] = value.strip()
                conflict_base_commit = conflict_values.get(
                    "EVANDER_DEPENDENCY_CONFLICT_BASE_COMMIT"
                )
                conflict_base_tree = conflict_values.get("EVANDER_DEPENDENCY_CONFLICT_BASE_TREE")
                failed_task_id = conflict_values.get("EVANDER_DEPENDENCY_CONFLICT_FAILED_TASK")
                failed_deliverable_id = conflict_values.get(
                    "EVANDER_DEPENDENCY_CONFLICT_FAILED_DELIVERABLE"
                )
                full_object_id = r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})"
                if (
                    conflict_base_commit is not None
                    and re.fullmatch(full_object_id, conflict_base_commit)
                    and conflict_base_tree is not None
                    and re.fullmatch(full_object_id, conflict_base_tree)
                    and failed_task_id
                    and failed_deliverable_id
                    and conflict_paths
                ):
                    conflict = {
                        "base_branch": base_ref,
                        "base_commit_sha": conflict_base_commit,
                        "base_tree_hash": conflict_base_tree,
                        "dependency_git_sources": [dict(row) for row in dependency_git_sources],
                        "failed_dependency_task_id": failed_task_id,
                        "failed_deliverable_id": failed_deliverable_id,
                        "conflict_paths": conflict_paths,
                    }
            raise DependencyGitPreparationError(
                task_id=task_id,
                message=error,
                conflict=conflict,
            )
        raise RuntimeError(error)
    base_commit_sha = next(
        (
            line.partition("=")[2].strip().casefold()
            for line in str(result.get("stdout") or "").splitlines()
            if line.startswith("EVANDER_BASE_COMMIT=")
            and re.fullmatch(
                r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})",
                line.partition("=")[2].strip(),
            )
        ),
        None,
    )
    if base_commit_sha is None:
        error = (
            "prepared task worktree did not report its immutable base commit; "
            f"preserve {worktree_path} and repair evander.baseCommit before retrying"
        )
        if dependency_git_sources or pinned_base_commit is not None:
            raise DependencyGitPreparationError(
                task_id=task_id,
                message=error,
            )
        raise RuntimeError(error)
    if composition_result is not None:
        for field_name, marker in (
            ("root_commit_sha", "EVANDER_COMPOSITION_ROOT_COMMIT="),
            ("composed_commit_sha", "EVANDER_COMPOSED_COMMIT="),
            ("composed_tree_hash", "EVANDER_COMPOSED_TREE="),
        ):
            value = next(
                (
                    line[len(marker) :].strip().casefold()
                    for line in str(result.get("stdout") or "").splitlines()
                    if line.startswith(marker)
                ),
                None,
            )
            if value is not None and re.fullmatch(
                r"(?:[0-9a-f]{40}|[0-9a-f]{64})",
                value,
            ):
                composition_result[field_name] = value
    if preparation_receipt_result is not None and preparation_attempt_id is not None:
        head_commit_sha = next(
            (
                line.strip().casefold()
                for line in reversed(str(result.get("stdout") or "").splitlines())
                if re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", line.strip())
            ),
            base_commit_sha,
        )
        material = json.dumps(
            {
                "attempt_id": preparation_attempt_id,
                "worktree_path": worktree_path,
                "branch": agent_branch,
                "base_commit_sha": base_commit_sha,
                "head_commit_sha": head_commit_sha,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        preparation_receipt_result.update(
            {
                "request_fingerprint": (
                    "libvirt:" + hashlib.sha256(material.encode("utf-8")).hexdigest()
                ),
                "head_commit_sha": head_commit_sha,
                "receipt_reused": False,
                "verified_clean": True,
            }
        )
    return worktree_path, base_commit_sha
