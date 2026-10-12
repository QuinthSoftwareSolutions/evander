"""Api release."""

from __future__ import annotations

from evander.git_refs import private_branch_ref

import json
import posixpath
import re
import shlex
import zipfile
from collections.abc import (
    Mapping,
)
from pathlib import (
    Path,
)
from typing import (
    Any,
)

from starlette.requests import (
    Request,
)

from evander.execution.container_worker_client import (
    ContainerWorkerClient,
    ContainerWorkerClientError,
)

from .models import (
    TeamState,
)


def _release_verification_live_log_limit(request: Request) -> int:
    raw_limit = request.query_params.get("limit", "160")
    try:
        parsed = int(raw_limit)
    except (TypeError, ValueError):
        return 160
    return max(20, min(parsed, 400))


def _release_verification_live_payload(
    state: TeamState,
    release_package_id: str,
    directory_path: str,
    provider_config: Any,
    limit: int,
) -> dict[str, Any]:
    """Read a bounded worker-log tail without ever returning worker credentials."""

    release = state.release_packages.get(release_package_id)
    if release is None:
        raise ValueError("release package not found")
    runs = [
        state.release_verification_runs[run_id]
        for run_id in release.verification_run_ids
        if run_id in state.release_verification_runs
        and state.release_verification_runs[run_id].superseded_by_verification_run_id is None
    ]
    runs.sort(key=lambda run: (run.attempt_number, run.created_at, run.id), reverse=True)
    run = runs[0] if runs else None
    if run is None:
        return {
            "release_package_id": release.id,
            "run": None,
            "log": {"available": False, "message": "No verification run has been created."},
        }
    command_index = (
        min(max(run.current_command_index, 0), len(run.command_results) - 1)
        if run.command_results
        else 0
    )
    command = run.command_results[command_index] if run.command_results else None
    run_payload: dict[str, Any] = {
        "id": run.id,
        "state": run.state,
        "attempt_number": run.attempt_number,
        "current_command": (
            {
                "index": command_index,
                "name": command.name,
                "state": command.state,
            }
            if command is not None
            else None
        ),
    }
    if run.state != "running" or command is None or not command.job_id:
        return {
            "release_package_id": release.id,
            "run": run_payload,
            "log": {
                "available": False,
                "message": (
                    "Waiting for the verifier to start the current command."
                    if run.state == "running"
                    else "The verifier is not currently running."
                ),
            },
        }
    try:
        config = provider_config.resolve_for_directory(directory_path)
        worker_url = str(getattr(config, "container_worker_base_url", "") or "").strip()
        if not worker_url:
            raise ValueError("container worker is not configured")
        worker_client = ContainerWorkerClient(
            base_url=worker_url,
            token_env=str(getattr(config, "container_worker_token_env", "EVANDER_WORKER_TOKEN")),
            timeout_seconds=float(
                getattr(config, "container_worker_request_timeout_seconds", 30.0)
            ),
        )
        log_text, metadata = worker_client.logs_page(
            command.job_id,
            mode="raw",
            limit=limit,
        )
    except (ContainerWorkerClientError, ValueError, TypeError, AttributeError):
        return {
            "release_package_id": release.id,
            "run": run_payload,
            "log": {
                "available": False,
                "message": "Live verifier output is temporarily unavailable.",
            },
        }
    return {
        "release_package_id": release.id,
        "run": run_payload,
        "log": {
            "available": True,
            "text": log_text,
            "returned_lines": metadata.get("returned_lines"),
            "has_more_before": bool(metadata.get("has_more_before")),
        },
    }


def _release_candidate_checkout_metadata(
    managed_git_admin: Any | None,
    state: TeamState,
    release: Any,
    *,
    runtime_dir: str | Path,
) -> dict[str, str] | None:
    project = state.projects.get(str(getattr(release, "project_id", "") or ""))
    git = getattr(project, "git", None)
    candidate_sha = str(getattr(release, "candidate_commit_sha", "") or "").strip().lower()
    candidate_tree = str(getattr(release, "candidate_tree_hash", "") or "").strip().lower()
    release_branch = str(getattr(release, "release_branch", "") or "").strip()
    release_id = str(getattr(release, "id", "") or "").strip()
    if (
        git is None
        or not candidate_sha
        or not candidate_tree
        or not release_branch
        or not release_id
        or managed_git_admin is None
    ):
        return None
    get_repository = getattr(managed_git_admin, "get_repository", None)
    if not callable(get_repository):
        return None
    try:
        repository = get_repository(
            git.managed_repo_id,
            directory_path=git.directory_path,
        )
    except Exception:
        return None
    if not isinstance(repository, Mapping):
        return None
    canonical_identity = str(repository.get("canonical_repository_identity") or "").strip()
    if not canonical_identity:
        return None
    release_ref = (
        private_branch_ref(release_branch)
    )
    safe_release_id = re.sub(r"[^A-Za-z0-9._-]+", "-", release_id).strip(".-")
    suggested_target = f"./release-candidate-{safe_release_id or 'review'}"
    checkout_command = shlex.join(
        [
            "evander-cli",
            "team",
            "checkout-release-candidate",
            release_id,
            "--target",
            suggested_target,
            "--expected-commit-sha",
            candidate_sha,
            "--runtime-dir",
            str(Path(runtime_dir).expanduser().resolve(strict=False)),
        ]
    )
    return {
        "canonical_repository_identity": canonical_identity,
        "repository_display_name": str(repository.get("display_name") or "").strip(),
        "managed_repo_id": git.managed_repo_id,
        "release_ref": release_ref,
        "candidate_commit_sha": candidate_sha,
        "candidate_tree_hash": candidate_tree,
        "checkout_command": checkout_command,
    }


def _human_review_bundle_manifest(
    *,
    human_request: Any,
    release: Any,
    project: Any | None,
    source_ref: str,
) -> dict[str, Any]:
    release_manifest = getattr(release, "manifest", None)
    release_manifest_payload = (
        release_manifest.model_dump(mode="json")
        if release_manifest is not None and callable(getattr(release_manifest, "model_dump", None))
        else None
    )
    requirement = getattr(human_request, "requirement_snapshot", None)
    return {
        "schema": "evander.team.human-review-bundle/v1",
        "release_package_id": str(getattr(release, "id", "") or ""),
        "human_review_request_id": str(getattr(human_request, "id", "") or ""),
        "project": {
            "id": str(getattr(project, "id", "") or ""),
            "name": str(getattr(project, "name", "") or ""),
            "directory_path": str(getattr(project, "directory_path", "") or ""),
        },
        "candidate": {
            "source_ref": source_ref,
            "commit_sha": str(getattr(release, "candidate_commit_sha", "") or ""),
            "tree_hash": str(getattr(release, "candidate_tree_hash", "") or ""),
        },
        "test_instructions": str(getattr(requirement, "test_instructions", "") or ""),
        "release_manifest": release_manifest_payload,
    }


HUMAN_REVIEW_BUNDLE_MANIFEST_PATH = "EVANDER-REVIEW.json"


def _human_review_bundle_member_reserves_manifest(member_name: str) -> bool:
    normalized = posixpath.normpath(str(member_name or "").replace("\\", "/"))
    return normalized.casefold() == HUMAN_REVIEW_BUNDLE_MANIFEST_PATH.casefold()


def _append_human_review_bundle_manifest(
    archive_path: Path,
    manifest: Mapping[str, Any],
) -> None:
    with zipfile.ZipFile(archive_path, mode="a", compression=zipfile.ZIP_DEFLATED) as archive:
        if any(
            _human_review_bundle_member_reserves_manifest(info.filename)
            for info in archive.infolist()
        ):
            raise ValueError(
                f"Release candidate contains reserved review-bundle path "
                f"{HUMAN_REVIEW_BUNDLE_MANIFEST_PATH!r}"
            )
        archive.writestr(
            HUMAN_REVIEW_BUNDLE_MANIFEST_PATH,
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        )


def _human_review_bundle_filename(release_package_id: str) -> str:
    safe_release_id = re.sub(
        r"[^A-Za-z0-9._-]+",
        "-",
        str(release_package_id or ""),
    ).strip(".-")
    return f"{safe_release_id or 'release'}-review-bundle.zip"


def _delete_temporary_file(path: str | Path) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass
