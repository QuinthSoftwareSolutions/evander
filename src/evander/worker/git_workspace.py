from __future__ import annotations

from evander.git_refs import managed_branch_ref

from evander.git_recovery import GitPreparationFailure

import json
import os
import re
import stat
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from evander.execution.task_worktree_cleanup import (
    cleanup_task_worktree as cleanup_task_worktree_checkout,
    task_worktree_repository_lock,
)
from evander.git_identity import (
    DEFAULT_AGENT_GIT_IDENTITY,
    TEAM_DEPENDENCY_COMPOSER_GIT_IDENTITY,
)

from .models import (
    WorkerReferenceRepoPrepareRecord,
    WorkerReferenceRepoPrepareRequest,
    WorkerSessionPaths,
    WorkerTaskWorktreeCleanupRecord,
    WorkerTaskWorktreeCleanupRequest,
    WorkerTaskWorktreeRecord,
    WorkerTaskWorktreeRequest,
    ensure_resolved_path_within,
)


class WorkerDependencyCompositionConflict(ValueError):
    """Structured, recoverable conflict while composing exact dependency commits."""

    def __init__(self, message: str, *, details: dict[str, Any]) -> None:
        super().__init__(message)
        self.details = details


class WorkerReferenceRepoTrustConflict(ValueError):
    """Structured conflict for a clean reference checkout that moved outside preparation."""

    def __init__(
        self,
        message: str,
        *,
        repo_name: str,
        current_commit: str,
        trusted_commit: str,
    ) -> None:
        super().__init__(message)
        self.details = {
            "code": "reference_repo_trust_mismatch",
            "repo_name": repo_name,
            "current_commit": current_commit,
            "trusted_commit": trusted_commit,
        }


def _repo_source_requires_git_ssh(repo_source: str | None) -> bool:
    if not repo_source:
        return False
    normalized = repo_source.strip()
    return normalized.startswith("ssh://") or bool(re.match(r"^[^/@:\s]+@[^:\s]+:.+", normalized))


def _git_subprocess_env(
    agent_home: Path,
    *,
    extra_config: Mapping[str, str] | None = None,
) -> dict[str, str]:
    host_config_path = _host_git_ssh_config_path(agent_home)
    try:
        host_config_stat = host_config_path.lstat()
    except FileNotFoundError:
        config_path = agent_home / ".ssh" / "config"
    else:
        if not stat.S_ISREG(host_config_stat.st_mode):
            raise ValueError("Host Git SSH config must be a regular file")
        config_path = host_config_path
    empty_hooks_path = agent_home.parent / "host-git-empty-hooks"
    try:
        empty_hooks_stat = empty_hooks_path.lstat()
    except FileNotFoundError:
        empty_hooks_path.mkdir(parents=True, mode=0o700)
    else:
        if not stat.S_ISDIR(empty_hooks_stat.st_mode) or stat.S_ISLNK(empty_hooks_stat.st_mode):
            raise ValueError("Host Git hooks path must be a directory")
        empty_hooks_path.chmod(0o700)

    env = os.environ.copy()
    for key in list(env):
        if (
            key in {"GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS"}
            or re.fullmatch(r"GIT_CONFIG_(?:KEY|VALUE)_\d+", key)
            or key.startswith("GIT_TRACE")
            or key in {"GIT_CURL_VERBOSE", "GIT_TRANSPORT_HELPER_DEBUG"}
        ):
            env.pop(key, None)
    env.update(
        {
            "HOME": str(agent_home),
            "GIT_SSH_COMMAND": f"ssh -F {config_path}",
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "never",
            "GIT_LFS_SKIP_SMUDGE": "1",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_ATTR_NOSYSTEM": "1",
        }
    )
    config = {
        "credential.helper": "",
        "core.hooksPath": str(empty_hooks_path),
        "core.fsmonitor": "false",
        "core.attributesFile": os.devnull,
        "filter.lfs.clean": "",
        "filter.lfs.smudge": "",
        "filter.lfs.process": "",
        "filter.lfs.required": "false",
        "protocol.ext.allow": "never",
        "submodule.recurse": "false",
    }
    config.update(extra_config or {})
    env["GIT_CONFIG_COUNT"] = str(len(config))
    for index, (key, value) in enumerate(config.items()):
        env[f"GIT_CONFIG_KEY_{index}"] = key
        env[f"GIT_CONFIG_VALUE_{index}"] = value
    return env


def _resolved_git_dir_within_checkout(
    target_dir: Path,
    *,
    env: Mapping[str, str],
) -> Path | None:
    metadata_path = target_dir / ".git"
    try:
        metadata_stat = metadata_path.lstat()
        resolved_target = target_dir.resolve(strict=True)
    except OSError:
        return None
    if not stat.S_ISDIR(metadata_stat.st_mode) or stat.S_ISLNK(metadata_stat.st_mode):
        return None
    completed = subprocess.run(
        ["git", "-C", str(target_dir), "rev-parse", "--absolute-git-dir"],
        env=dict(env),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return None
    try:
        resolved_git_dir = Path(completed.stdout.strip()).resolve(strict=True)
        resolved_git_dir.relative_to(resolved_target)
    except (OSError, ValueError):
        return None
    return resolved_git_dir


def _reference_repo_git_environment(target_dir: Path, *, agent_home: Path) -> dict[str, str]:
    base_env = _git_subprocess_env(agent_home)
    if _resolved_git_dir_within_checkout(target_dir, env=base_env) is None:
        raise ValueError("reference repo Git metadata is missing or unsafe")
    configured_filters = subprocess.run(
        [
            "git",
            "-C",
            str(target_dir),
            "config",
            "--local",
            "--name-only",
            "--get-regexp",
            r"^filter\..*\.(clean|smudge|process|required)$",
        ],
        env=base_env,
        check=False,
        capture_output=True,
        text=True,
    )
    if configured_filters.returncode not in {0, 1}:
        raise subprocess.CalledProcessError(
            configured_filters.returncode,
            configured_filters.args,
            output=configured_filters.stdout,
            stderr=configured_filters.stderr,
        )
    disabled_filters: dict[str, str] = {}
    for key in configured_filters.stdout.splitlines():
        match = re.fullmatch(r"(?P<prefix>filter\..*)\.(?:clean|smudge|process|required)", key)
        if match is None:
            continue
        prefix = match.group("prefix")
        disabled_filters.update(
            {
                f"{prefix}.clean": "",
                f"{prefix}.smudge": "",
                f"{prefix}.process": "",
                f"{prefix}.required": "false",
            }
        )
    return _git_subprocess_env(agent_home, extra_config=disabled_filters)


def _host_git_ssh_config_path(agent_home: Path) -> Path:
    return agent_home.parent / "host-ssh" / "config"


class GitWorkspaceOperations:
    """Reference checkout and task worktree preparation operations."""

    def _prepare_reference_repo_locked(
        self,
        paths: WorkerSessionPaths,
        request: WorkerReferenceRepoPrepareRequest,
    ) -> WorkerReferenceRepoPrepareRecord:
        target_dir = paths.repo_root / request.repo_name
        ensure_resolved_path_within(
            base=paths.repo_root,
            candidate=target_dir,
            description="reference repo target",
        )
        if target_dir.is_symlink() or (target_dir.exists() and not target_dir.is_dir()):
            raise ValueError(
                f"reference repo target is not a directory: /home/agent/repos/{request.repo_name}"
            )
        git_env = _git_subprocess_env(paths.agent_home)
        cloned = False
        populated_checkout = target_dir.exists() and any(target_dir.iterdir())
        if populated_checkout:
            if _resolved_git_dir_within_checkout(target_dir, env=git_env) is None:
                raise ValueError(
                    "reference repo Git metadata is missing or unsafe: "
                    f"/home/agent/repos/{request.repo_name}"
                )
            if not self._is_git_checkout_for_source(
                target_dir,
                request.repo_source,
                agent_home=paths.agent_home,
            ):
                raise ValueError(
                    "reference repo target is not a checkout for requested source: "
                    f"/home/agent/repos/{request.repo_name}"
                )
            if not self._is_prepared_reference_checkout(target_dir, git_env=git_env):
                try:
                    current_commit = self._git_commit_sha(target_dir, paths.agent_home)
                except subprocess.CalledProcessError as exc:
                    raise ValueError(
                        "existing ordinary repository checkout has no commit: "
                        f"/home/agent/repos/{request.repo_name}"
                    ) from exc
                return WorkerReferenceRepoPrepareRecord(
                    session_id=paths.session_id,
                    target_dir=f"/home/agent/repos/{request.repo_name}",
                    guest_repo_name=request.repo_name,
                    remote_url=request.repo_source,
                    source_ref=request.source_ref,
                    commit_sha=current_commit,
                    cloned=False,
                    checkout_updated=False,
                    configuration_updated=False,
                )
            reused = self._reuse_reference_repo_preparation(
                paths=paths,
                request=request,
                target_dir=target_dir,
            )
            if reused is not None:
                return reused

        self._sync_git_ssh_into_agent_home(
            paths, required=_repo_source_requires_git_ssh(request.repo_source)
        )
        if not populated_checkout:
            target_dir.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["git", "init", "--quiet", str(target_dir)],
                env=git_env,
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "-C", str(target_dir), "remote", "add", "origin", request.repo_source],
                env=git_env,
                check=True,
                capture_output=True,
                text=True,
            )
            cloned = True

        git_env = _reference_repo_git_environment(target_dir, agent_home=paths.agent_home)
        try:
            current_commit = self._git_commit_sha(target_dir, paths.agent_home)
        except subprocess.CalledProcessError:
            current_commit = None
        if current_commit is None and request.commit_sha is None:
            raise ValueError(
                f"reference repo {request.repo_name} is missing and no cached commit is "
                "available; set refresh_upstream=true"
            )
        has_tracked_changes = self._reference_repo_has_tracked_changes(
            paths=paths,
            target_dir=target_dir,
            expected_commit=current_commit,
        )
        if has_tracked_changes:
            raise ValueError(
                f"reference repo checkout is dirty and cannot be prepared: {request.repo_name}"
            )
        stored_trusted_commit = self._read_reference_repo_trusted_commit(paths, request)
        trusted_commit = stored_trusted_commit
        if current_commit is not None and not request.update_existing:
            if trusted_commit is None:
                if current_commit != request.commit_sha:
                    raise ValueError(
                        f"reference repo {request.repo_name} is at untrusted commit "
                        f"{current_commit} and has no matching trusted prepared state; "
                        "set refresh_upstream=true"
                    )
                trusted_commit = current_commit
            elif current_commit != trusted_commit:
                raise WorkerReferenceRepoTrustConflict(
                    f"reference repo {request.repo_name} is at untrusted commit "
                    f"{current_commit}; last trusted prepared commit is {trusted_commit}",
                    repo_name=request.repo_name,
                    current_commit=current_commit,
                    trusted_commit=trusted_commit,
                )
        detached = (
            subprocess.run(
                ["git", "-C", str(target_dir), "symbolic-ref", "-q", "HEAD"],
                env=git_env,
                check=False,
                capture_output=True,
                text=True,
            ).returncode
            != 0
        )
        fetch_requested_commit = current_commit is None or (
            request.update_existing and current_commit != request.commit_sha
        )
        checkout_updated = fetch_requested_commit or not detached
        expected_commit: str
        if fetch_requested_commit:
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(target_dir),
                    "fetch",
                    "--no-tags",
                    request.repo_source,
                    request.source_ref,
                ],
                env=git_env,
                check=True,
                capture_output=True,
                text=True,
            )
            fetched_commit = (
                subprocess.run(
                    ["git", "-C", str(target_dir), "rev-parse", "FETCH_HEAD"],
                    env=git_env,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                .stdout.strip()
                .lower()
            )
            if request.update_existing and fetched_commit != request.commit_sha:
                raise ValueError(
                    f"reference repo {request.repo_name} fetched {fetched_commit}, "
                    f"expected {request.commit_sha}"
                )
            expected_commit = request.commit_sha if request.update_existing else fetched_commit
            if expected_commit is None:
                raise RuntimeError("reference repo expected commit unexpectedly missing")
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(target_dir),
                    "checkout",
                    "--quiet",
                    "--no-overwrite-ignore",
                    "--detach",
                    expected_commit,
                ],
                env=git_env,
                check=True,
                capture_output=True,
                text=True,
            )
        else:
            expected_commit = request.commit_sha if request.update_existing else trusted_commit
            if expected_commit is None:
                raise RuntimeError("reference repo expected commit unexpectedly missing")
            if not detached:
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(target_dir),
                        "checkout",
                        "--quiet",
                        "--no-overwrite-ignore",
                        "--detach",
                        expected_commit,
                    ],
                    env=git_env,
                    check=True,
                    capture_output=True,
                    text=True,
                )

        current_version = subprocess.run(
            [
                "git",
                "-C",
                str(target_dir),
                "config",
                "--get",
                "evander.referenceRepoConfigVersion",
            ],
            env=git_env,
            check=False,
            capture_output=True,
            text=True,
        ).stdout.strip()
        configuration_updated = current_version != request.configuration_version
        if configuration_updated:
            config_commands = (
                ("remote", "set-url", "origin", request.repo_source),
                ("remote", "remove", "agent"),
                ("config", "--unset-all", "alias.push-agent"),
                ("config", "evander.pushAgentAvailable", "false"),
                ("config", "evander.gitAccessMode", "read-only"),
                ("config", "evander.referenceRepo", "true"),
                (
                    "config",
                    "evander.referenceRepoConfigVersion",
                    request.configuration_version,
                ),
            )
            for command in config_commands:
                completed = subprocess.run(
                    ["git", "-C", str(target_dir), *command],
                    env=git_env,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if completed.returncode != 0 and command not in {
                    ("remote", "remove", "agent"),
                    ("config", "--unset-all", "alias.push-agent"),
                }:
                    raise subprocess.CalledProcessError(
                        completed.returncode,
                        completed.args,
                        output=completed.stdout,
                        stderr=completed.stderr,
                    )

        resolved_commit = self._git_commit_sha(target_dir, paths.agent_home)
        if resolved_commit != expected_commit:
            if request.update_existing:
                raise ValueError(
                    f"reference repo {request.repo_name} is at {resolved_commit}, "
                    f"expected {expected_commit}"
                )
            raise ValueError(
                f"reference repo {request.repo_name} resolved to untrusted commit "
                f"{resolved_commit}; expected trusted commit {expected_commit}"
            )
        if stored_trusted_commit != resolved_commit:
            self._write_reference_repo_trusted_commit(paths, request, resolved_commit)
        record = WorkerReferenceRepoPrepareRecord(
            session_id=paths.session_id,
            target_dir=f"/home/agent/repos/{request.repo_name}",
            guest_repo_name=request.repo_name,
            remote_url=request.repo_source,
            source_ref=request.source_ref,
            commit_sha=resolved_commit,
            cloned=cloned,
            checkout_updated=checkout_updated,
            configuration_updated=configuration_updated,
        )
        if request.preparation_fingerprint is not None:
            self._write_preparation_receipt(
                paths,
                kind="reference-repo",
                name=request.repo_name,
                payload={
                    "version": 1,
                    "request_fingerprint": request.preparation_fingerprint,
                    "repo_source": request.repo_source,
                    "repo_name": request.repo_name,
                    "source_ref": request.source_ref,
                    "requested_commit_sha": request.commit_sha,
                    "checkout_commit_sha": resolved_commit,
                    "configuration_version": request.configuration_version,
                },
            )
        return record

    def _prepare_task_worktree_locked(
        self,
        paths: WorkerSessionPaths,
        request: WorkerTaskWorktreeRequest,
    ) -> WorkerTaskWorktreeRecord:
        repo_dir = paths.repo_root / request.repo_name
        ensure_resolved_path_within(
            base=paths.repo_root,
            candidate=repo_dir,
            description="task worktree repository",
        )
        if not (repo_dir / ".git").exists():
            raise ValueError(f"session repository does not exist: {request.repo_name}")
        worktrees_root = paths.agent_home / "worktrees" / request.repo_name
        target_dir = worktrees_root / request.worktree_name
        ensure_resolved_path_within(
            base=paths.agent_home,
            candidate=target_dir,
            description="task worktree target",
        )
        target_exists = target_dir.exists()
        requested_expected_base_commit_sha = request.expected_base_commit_sha
        requested_expected_base_tree_hash = request.expected_base_tree_hash
        preparation_fingerprint = self._task_worktree_preparation_fingerprint(request)
        preparation_receipt = self._read_task_worktree_preparation_receipt(
            paths,
            request.preparation_attempt_id,
        )
        if preparation_receipt is not None:
            if preparation_receipt.get("request_fingerprint") != preparation_fingerprint:
                raise GitPreparationFailure(
                    "task worktree preparation attempt was already used with a different request", cause="workspace_receipt_mismatch")
            for field_name, expected in (
                ("repo_name", request.repo_name),
                ("worktree_name", request.worktree_name),
                ("branch", request.branch),
                (
                    "target_dir",
                    f"/home/agent/worktrees/{request.repo_name}/{request.worktree_name}",
                ),
            ):
                if preparation_receipt.get(field_name) != expected:
                    raise GitPreparationFailure(
                        f"task worktree preparation receipt {field_name} does not match request", cause="workspace_receipt_mismatch")
            if not target_exists:
                raise GitPreparationFailure(
                    "task worktree preparation receipt exists but its checkout is missing; "
                    "use a new preparation generation", cause="workspace_missing")
            receipt_base = str(preparation_receipt.get("base_commit_sha") or "").strip()
            if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", receipt_base) is None:
                raise GitPreparationFailure("task worktree preparation receipt has no authoritative base", cause="workspace_receipt_mismatch")
            requested_base = preparation_receipt.get("requested_expected_base_commit_sha")
            if request.expected_base_commit_sha not in {None, requested_base, receipt_base}:
                raise GitPreparationFailure(
                    "task worktree preparation request conflicts with its receipted base", cause="workspace_receipt_mismatch")
            requested_tree = preparation_receipt.get("requested_expected_base_tree_hash")
            if request.expected_base_tree_hash not in {None, requested_tree}:
                raise GitPreparationFailure(
                    "task worktree preparation request conflicts with its receipted base tree", cause="workspace_receipt_mismatch")
            # The first call already verified any pinned composition root and
            # tree. Replay validates the resulting host-owned receipt and the
            # live checkout against its final base.
            request = request.model_copy(
                update={
                    "expected_base_commit_sha": receipt_base,
                    "expected_base_tree_hash": None,
                }
            )
        if target_exists and request.expected_base_commit_sha is None:
            raise GitPreparationFailure(
                "existing task worktree has no authoritative server-recorded "
                "base; preserve the checkout and use an audited provenance "
                "recovery", cause="workspace_receipt_mismatch")
        env = _git_subprocess_env(paths.agent_home)
        if preparation_receipt is not None and self._task_worktree_has_user_changes(
            target_dir,
            git_env=env,
        ):
            raise GitPreparationFailure(
                "task worktree preparation receipt cannot be replayed because the checkout "
                "contains user changes; preserve it for reviewed recovery", cause="workspace_dirty")
        self._validate_git_branch(repo_dir, request.branch, env=env)
        subprocess.run(
            ["git", "config", "extensions.worktreeConfig", "true"],
            cwd=repo_dir,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        created = False
        base_commit_sha: str | None = None
        if not target_exists and request.expected_base_commit_sha is not None:
            if request.expected_base_tree_hash is None:
                base_commit_sha = self._resolve_authoritative_task_base_commit(
                    repo_dir,
                    request.base_ref,
                    expected_base_commit_sha=request.expected_base_commit_sha,
                    env=env,
                )
            else:
                base_commit_sha = self._resolve_fresh_task_base_commit(
                    repo_dir,
                    request.base_ref,
                    env=env,
                )
                self._verify_task_base_provenance(
                    repo_dir=repo_dir,
                    base_ref=request.base_ref,
                    base_commit_sha=base_commit_sha,
                    expected_base_commit_sha=request.expected_base_commit_sha,
                    expected_base_tree_hash=request.expected_base_tree_hash,
                    env=env,
                )
        if target_exists:
            self._make_task_worktree_metadata_portable(
                repo_dir=repo_dir,
                target_dir=target_dir,
                branch=request.branch,
            )
            current_branch = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=target_dir,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            if current_branch != request.branch:
                raise ValueError(
                    f"task worktree {request.worktree_name} uses branch "
                    f"{current_branch!r}, expected {request.branch!r}"
                )
        else:
            worktrees_root.mkdir(parents=True, exist_ok=True)
            local_branch = subprocess.run(
                ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{request.branch}"],
                cwd=repo_dir,
                env=env,
                check=False,
            )
            args = ["git", "worktree", "add"]
            if local_branch.returncode == 0:
                if request.expected_base_commit_sha is None:
                    raise GitPreparationFailure(
                        "reused task branch has no authoritative server-recorded "
                        "base; preserve the branch and use an audited provenance "
                        "recovery", cause="workspace_receipt_mismatch")
                args.extend([str(target_dir), request.branch])
            else:
                if base_commit_sha is None:
                    base_commit_sha = self._resolve_fresh_task_base_commit(
                        repo_dir,
                        request.base_ref,
                        env=env,
                    )
                self._verify_task_base_provenance(
                    repo_dir=repo_dir,
                    base_ref=request.base_ref,
                    base_commit_sha=base_commit_sha,
                    expected_base_commit_sha=request.expected_base_commit_sha,
                    expected_base_tree_hash=request.expected_base_tree_hash,
                    env=env,
                )
                args.extend(["-b", request.branch, str(target_dir), base_commit_sha])
            subprocess.run(
                args,
                cwd=repo_dir,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            created = True
            self._make_task_worktree_metadata_portable(
                repo_dir=repo_dir,
                target_dir=target_dir,
                branch=request.branch,
            )
        for key, value in (
            ("evander.workBranch", request.branch),
            ("evander.baseBranch", request.base_ref),
        ):
            subprocess.run(
                ["git", "config", "--worktree", key, value],
                cwd=target_dir,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
        configured_base_commit = subprocess.run(
            ["git", "config", "--worktree", "--get", "evander.baseCommit"],
            cwd=target_dir,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        if configured_base_commit.returncode == 0:
            candidate = configured_base_commit.stdout.strip().casefold()
            if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", candidate):
                if (
                    request.expected_base_commit_sha is not None
                    and candidate != request.expected_base_commit_sha
                ):
                    raise GitPreparationFailure(
                        "task worktree base commit does not match authoritative "
                        f"prior Team provenance: found {candidate}, expected "
                        f"{request.expected_base_commit_sha}", cause="workspace_receipt_mismatch")
                base_commit_sha = candidate
        if (
            request.expected_base_commit_sha is not None
            and base_commit_sha is not None
            and base_commit_sha != request.expected_base_commit_sha
        ):
            raise GitPreparationFailure(
                "task worktree base commit does not match authoritative prior "
                f"Team provenance: found {base_commit_sha}, expected "
                f"{request.expected_base_commit_sha}", cause="workspace_receipt_mismatch")
        if request.expected_base_commit_sha is not None:
            expected_base_is_ancestor = subprocess.run(
                [
                    "git",
                    "merge-base",
                    "--is-ancestor",
                    request.expected_base_commit_sha,
                    "HEAD",
                ],
                cwd=target_dir,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            if expected_base_is_ancestor.returncode != 0:
                raise GitPreparationFailure(
                    "authoritative prior Team base commit is not an ancestor of "
                    "the retained task worktree; preserve the checkout for "
                    "operator recovery", cause="workspace_receipt_mismatch")
            if base_commit_sha is None:
                base_commit_sha = request.expected_base_commit_sha
        if request.expected_base_tree_hash is not None:
            if base_commit_sha is None:
                raise GitPreparationFailure(
                    "task worktree base tree cannot be verified without an "
                    "authoritative base commit", cause="workspace_receipt_mismatch")
            self._verify_task_base_provenance(
                repo_dir=repo_dir,
                base_ref=request.base_ref,
                base_commit_sha=base_commit_sha,
                expected_base_commit_sha=request.expected_base_commit_sha,
                expected_base_tree_hash=request.expected_base_tree_hash,
                env=env,
            )
        composition_root_commit_sha: str | None = None
        configured_composed_commit_sha: str | None = None
        if request.dependency_git_sources:
            configured_composition_root = subprocess.run(
                [
                    "git",
                    "config",
                    "--worktree",
                    "--get",
                    "evander.dependencyCompositionRootCommit",
                ],
                cwd=target_dir,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            candidate_root = configured_composition_root.stdout.strip().casefold()
            if configured_composition_root.returncode == 0 and re.fullmatch(
                r"(?:[0-9a-f]{40}|[0-9a-f]{64})",
                candidate_root,
            ):
                composition_root_commit_sha = candidate_root
            else:
                composition_root_commit_sha = self._git_commit_sha(
                    target_dir,
                    paths.agent_home,
                ).casefold()
            configured_composed_commit = subprocess.run(
                [
                    "git",
                    "config",
                    "--worktree",
                    "--get",
                    "evander.dependencyComposedCommit",
                ],
                cwd=target_dir,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            candidate_composed = configured_composed_commit.stdout.strip().casefold()
            if configured_composed_commit.returncode == 0 and re.fullmatch(
                r"(?:[0-9a-f]{40}|[0-9a-f]{64})",
                candidate_composed,
            ):
                configured_composed_commit_sha = candidate_composed
        base_commit_sha = self._compose_task_dependency_git_sources(
            repo_dir=repo_dir,
            target_dir=target_dir,
            base_ref=request.base_ref,
            dependency_git_sources=request.dependency_git_sources,
            created=created,
            base_commit_sha=base_commit_sha,
            env=env,
            dependency_merge_author_name=(
                request.dependency_merge_author_name or DEFAULT_AGENT_GIT_IDENTITY.name
            ),
            dependency_merge_author_email=(
                request.dependency_merge_author_email or DEFAULT_AGENT_GIT_IDENTITY.email
            ),
            dependency_composition_commit_message=(
                request.dependency_composition_commit_message
                or "Compose accepted Team dependencies"
            ),
            dependency_composition_mode=request.dependency_composition_mode,
            dependency_composition_recovery_id=(request.dependency_composition_recovery_id),
            resolved_dependency_branch=request.resolved_dependency_branch,
            resolved_dependency_commit_sha=request.resolved_dependency_commit_sha,
        )
        composed_commit_sha: str | None = None
        composed_tree_hash: str | None = None
        if composition_root_commit_sha is not None:
            current_head = self._git_commit_sha(
                target_dir,
                paths.agent_home,
            ).casefold()
            composed_commit_sha = configured_composed_commit_sha
            if composed_commit_sha is None:
                candidate_composed = (base_commit_sha or current_head).casefold()
                candidate_is_retained = all(
                    subprocess.run(
                        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
                        cwd=target_dir,
                        env=env,
                        check=False,
                        capture_output=True,
                        text=True,
                    ).returncode
                    == 0
                    for ancestor, descendant in (
                        (composition_root_commit_sha, candidate_composed),
                        (candidate_composed, current_head),
                    )
                )
                composed_commit_sha = candidate_composed if candidate_is_retained else current_head
            for ancestor, descendant, label in (
                (
                    composition_root_commit_sha,
                    composed_commit_sha,
                    "composition root",
                ),
                (composed_commit_sha, current_head, "composed dependency base"),
            ):
                if (
                    subprocess.run(
                        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
                        cwd=target_dir,
                        env=env,
                        check=False,
                        capture_output=True,
                        text=True,
                    ).returncode
                    != 0
                ):
                    raise ValueError(
                        f"retained {label} {ancestor} is not an ancestor of {descendant}"
                    )
            for key, value in (
                ("evander.dependencyCompositionRootCommit", composition_root_commit_sha),
                ("evander.dependencyComposedCommit", composed_commit_sha),
            ):
                subprocess.run(
                    ["git", "config", "--worktree", key, value],
                    cwd=target_dir,
                    env=env,
                    check=True,
                    capture_output=True,
                    text=True,
                )
            composed_tree_hash = (
                subprocess.run(
                    ["git", "rev-parse", f"{composed_commit_sha}^{{tree}}"],
                    cwd=target_dir,
                    env=env,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                .stdout.strip()
                .casefold()
            )
        if base_commit_sha is not None:
            subprocess.run(
                ["git", "config", "--worktree", "evander.baseCommit", base_commit_sha],
                cwd=target_dir,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
        if request.git_author_name is not None and request.git_author_email is not None:
            for key, value in (
                ("author.name", request.git_author_name),
                ("author.email", request.git_author_email),
            ):
                subprocess.run(
                    ["git", "config", "--worktree", key, value],
                    cwd=target_dir,
                    env=env,
                    check=True,
                    capture_output=True,
                    text=True,
                )
        self._reconcile_task_worktree_helpers(
            trusted_root=paths.agent_home,
            helper_source=repo_dir / ".evander",
            helper_target=target_dir / ".evander",
        )
        commit_sha = self._git_commit_sha(target_dir, paths.agent_home)
        if base_commit_sha is not None and commit_sha.casefold() == base_commit_sha.casefold():
            self._publish_initial_task_branch(
                target_dir=target_dir,
                branch=request.branch,
                env=env,
            )
        record = WorkerTaskWorktreeRecord(
            session_id=paths.session_id,
            repo_name=request.repo_name,
            target_dir=(f"/home/agent/worktrees/{request.repo_name}/{request.worktree_name}"),
            branch=request.branch,
            commit_sha=commit_sha,
            base_commit_sha=base_commit_sha,
            composition_root_commit_sha=composition_root_commit_sha,
            composed_commit_sha=composed_commit_sha,
            composed_tree_hash=composed_tree_hash,
            created=created,
            preparation_attempt_id=request.preparation_attempt_id,
            preparation_request_fingerprint=(
                preparation_fingerprint if request.preparation_attempt_id is not None else None
            ),
            preparation_receipt_reused=preparation_receipt is not None,
            verified_clean=True,
        )
        if preparation_receipt is not None:
            for field_name in ("commit_sha", "base_commit_sha"):
                expected_value = preparation_receipt.get(field_name)
                actual_value = getattr(record, field_name)
                if expected_value != actual_value:
                    raise ValueError(
                        f"task worktree preparation receipt {field_name} no longer matches checkout"
                    )
        elif request.preparation_attempt_id is not None:
            self._write_task_worktree_preparation_receipt(
                paths,
                record,
                request_fingerprint=preparation_fingerprint,
                requested_expected_base_commit_sha=(requested_expected_base_commit_sha),
                requested_expected_base_tree_hash=requested_expected_base_tree_hash,
            )
        return record

    @staticmethod
    def _compose_task_dependency_git_sources(
        *,
        repo_dir: Path,
        target_dir: Path,
        base_ref: str,
        dependency_git_sources: Sequence[Any],
        created: bool,
        base_commit_sha: str | None,
        env: dict[str, str],
        dependency_merge_author_name: str,
        dependency_merge_author_email: str,
        dependency_composition_commit_message: str,
        dependency_composition_mode: str,
        dependency_composition_recovery_id: str | None,
        resolved_dependency_branch: str | None,
        resolved_dependency_commit_sha: str | None,
    ) -> str | None:
        """Compose exact dependency commits before exposing a task worktree.

        The compact JSON stored in worktree-local Git configuration is immutable
        provenance. A reused checkout must name precisely the same sources; an
        older checkout that already contains task work cannot be retrofitted
        silently.
        """

        expected_rows = [
            source.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
            if hasattr(source, "model_dump")
            else dict(source)
            for source in dependency_git_sources
        ]
        expected_json = json.dumps(
            expected_rows,
            sort_keys=True,
            separators=(",", ":"),
        )

        def fetch_exact_ref(ref: str, expected_commit: str, *, label: str) -> None:
            subprocess.run(
                ["git", "fetch", "--no-tags", "origin", ref],
                cwd=repo_dir,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            fetched = (
                subprocess.run(
                    ["git", "rev-parse", "--verify", "FETCH_HEAD^{commit}"],
                    cwd=repo_dir,
                    env=env,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                .stdout.strip()
                .casefold()
            )
            if fetched != expected_commit:
                raise ValueError(
                    f"dependency source {label!r} moved: expected accepted "
                    f"commit {expected_commit}, found {fetched}"
                )

        def fetch_exact_source(source: Mapping[str, Any]) -> None:
            branch = str(source["branch"])
            pinned_ref = str(source.get("pinned_ref") or "").strip()
            fetch_exact_ref(
                pinned_ref or managed_branch_ref(branch),
                str(source["commit_sha"]),
                label=pinned_ref or branch,
            )

        if dependency_composition_mode == "manual_recovery":
            recovery_payload = json.dumps(
                {
                    "recovery_id": dependency_composition_recovery_id,
                    "dependency_git_sources": expected_rows,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            configured_recovery = subprocess.run(
                [
                    "git",
                    "config",
                    "--worktree",
                    "--get",
                    "evander.manualDependencyGitSources",
                ],
                cwd=target_dir,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            if configured_recovery.returncode == 0:
                if configured_recovery.stdout.strip() != recovery_payload:
                    raise ValueError(
                        "manual dependency recovery worktree does not match the "
                        "current immutable recovery sources"
                    )
                return base_commit_sha
            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=target_dir,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            head = (
                subprocess.run(
                    ["git", "rev-parse", "HEAD^{commit}"],
                    cwd=target_dir,
                    env=env,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                .stdout.strip()
                .casefold()
            )
            if status.strip() or (
                not created and (base_commit_sha is None or head != base_commit_sha)
            ):
                raise ValueError(
                    "manual dependency recovery requires a clean worktree at its "
                    "authoritative base before source preparation"
                )
            for source in expected_rows:
                fetch_exact_source(source)
            subprocess.run(
                [
                    "git",
                    "config",
                    "--worktree",
                    "evander.manualDependencyGitSources",
                    recovery_payload,
                ],
                cwd=target_dir,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            return base_commit_sha

        if dependency_composition_mode == "adopt_resolved":
            if base_commit_sha is None:
                raise ValueError("resolved dependency adoption requires an authoritative base")
            resolved_commit = str(resolved_dependency_commit_sha)
            fetch_exact_ref(
                managed_branch_ref(resolved_dependency_branch),
                resolved_commit,
                label=str(resolved_dependency_branch),
            )
            for source in expected_rows:
                fetch_exact_source(source)
            for required_commit, description in [
                (base_commit_sha, "authoritative base"),
                *[
                    (str(source["commit_sha"]), f"dependency {source['dependency_task_id']}")
                    for source in expected_rows
                ],
            ]:
                ancestor = subprocess.run(
                    ["git", "merge-base", "--is-ancestor", required_commit, resolved_commit],
                    cwd=target_dir,
                    env=env,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if ancestor.returncode != 0:
                    raise ValueError(
                        f"resolved dependency commit does not contain {description} "
                        f"commit {required_commit}"
                    )
            head = (
                subprocess.run(
                    ["git", "rev-parse", "HEAD^{commit}"],
                    cwd=target_dir,
                    env=env,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                .stdout.strip()
                .casefold()
            )
            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=target_dir,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            if status.strip() or head not in {base_commit_sha, resolved_commit}:
                raise ValueError(
                    "resolved dependency commit cannot replace retained authored task work"
                )
            subprocess.run(
                ["git", "reset", "--hard", resolved_commit],
                cwd=target_dir,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            for key, value in (
                ("evander.dependencyGitProvenance", expected_json),
                ("evander.dependencyCompositionRecovery", str(dependency_composition_recovery_id)),
            ):
                subprocess.run(
                    ["git", "config", "--worktree", key, value],
                    cwd=target_dir,
                    env=env,
                    check=True,
                    capture_output=True,
                    text=True,
                )
            return resolved_commit

        configured = subprocess.run(
            [
                "git",
                "config",
                "--worktree",
                "--get",
                "evander.dependencyGitProvenance",
            ],
            cwd=target_dir,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        configured_json = configured.stdout.strip() if configured.returncode == 0 else ""
        if configured_json:
            if configured_json != expected_json:
                raise ValueError(
                    "task worktree dependency Git provenance does not match the "
                    "current direct dependencies; preserve the checkout and create "
                    "an explicit correction or replacement task"
                )
            head = (
                subprocess.run(
                    ["git", "rev-parse", "HEAD^{commit}"],
                    cwd=target_dir,
                    env=env,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                .stdout.strip()
                .casefold()
            )
            for source in expected_rows:
                ancestor = subprocess.run(
                    [
                        "git",
                        "merge-base",
                        "--is-ancestor",
                        str(source["commit_sha"]),
                        head,
                    ],
                    cwd=target_dir,
                    env=env,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if ancestor.returncode != 0:
                    raise ValueError(
                        "task worktree dependency provenance is corrupt: accepted "
                        f"commit {source['commit_sha']} for dependency "
                        f"{source['dependency_task_id']} is not an ancestor of HEAD"
                    )
            return base_commit_sha
        if not expected_rows:
            return base_commit_sha

        head_before = (
            subprocess.run(
                ["git", "rev-parse", "HEAD^{commit}"],
                cwd=target_dir,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            .stdout.strip()
            .casefold()
        )
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=target_dir,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        if status.strip():
            raise ValueError(
                "task worktree must be clean before adopting or composing dependency Git provenance"
            )
        all_dependencies_are_ancestors = True
        for source in expected_rows:
            expected_commit = str(source["commit_sha"])
            fetch_exact_source(source)
            ancestor = subprocess.run(
                ["git", "merge-base", "--is-ancestor", expected_commit, head_before],
                cwd=target_dir,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            if ancestor.returncode != 0:
                all_dependencies_are_ancestors = False
        if all_dependencies_are_ancestors:
            if base_commit_sha is None:
                raise ValueError(
                    "retained task worktree contains every accepted dependency "
                    "commit but has no authoritative prior Team base commit; "
                    "preserve it for operator recovery"
                )
            subprocess.run(
                [
                    "git",
                    "config",
                    "--worktree",
                    "evander.dependencyGitProvenance",
                    expected_json,
                ],
                cwd=target_dir,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            return base_commit_sha
        if not created and (base_commit_sha is None or head_before != base_commit_sha):
            raise ValueError(
                "existing task worktree has no dependency Git provenance and "
                "does not already contain every accepted dependency commit; "
                "preserve it for explicit recovery"
            )

        def is_ancestor(ancestor: str, descendant: str) -> bool:
            return (
                subprocess.run(
                    ["git", "merge-base", "--is-ancestor", ancestor, descendant],
                    cwd=target_dir,
                    env=env,
                    check=False,
                    capture_output=True,
                    text=True,
                ).returncode
                == 0
            )

        frontier_rows: list[dict[str, Any]] = []
        seen_frontier_commits: set[str] = set()
        for source in expected_rows:
            expected_commit = str(source["commit_sha"])
            if expected_commit in seen_frontier_commits or is_ancestor(
                expected_commit, head_before
            ):
                continue
            if any(
                expected_commit != str(other["commit_sha"])
                and is_ancestor(expected_commit, str(other["commit_sha"]))
                for other in expected_rows
            ):
                continue
            frontier_rows.append(source)
            seen_frontier_commits.add(expected_commit)

        composition_env = {
            **env,
            "GIT_AUTHOR_NAME": dependency_merge_author_name,
            "GIT_AUTHOR_EMAIL": dependency_merge_author_email,
            "GIT_COMMITTER_NAME": TEAM_DEPENDENCY_COMPOSER_GIT_IDENTITY.name,
            "GIT_COMMITTER_EMAIL": TEAM_DEPENDENCY_COMPOSER_GIT_IDENTITY.email,
            "GIT_MERGE_AUTOEDIT": "no",
        }
        try:
            if len(frontier_rows) == 1 and is_ancestor(
                head_before, str(frontier_rows[0]["commit_sha"])
            ):
                subprocess.run(
                    ["git", "reset", "--hard", str(frontier_rows[0]["commit_sha"])],
                    cwd=target_dir,
                    env=env,
                    check=True,
                    capture_output=True,
                    text=True,
                )
            elif frontier_rows:
                for source in frontier_rows:
                    expected_commit = str(source["commit_sha"])
                    merge = subprocess.run(
                        [
                            "git",
                            "merge",
                            "--no-ff",
                            "-m",
                            f"Temporary Team integration of {source['dependency_task_id']}",
                            expected_commit,
                        ],
                        cwd=target_dir,
                        env=composition_env,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    if merge.returncode != 0:
                        conflict_paths = [
                            line.strip()
                            for line in subprocess.run(
                                ["git", "diff", "--name-only", "--diff-filter=U"],
                                cwd=target_dir,
                                env=env,
                                check=False,
                                capture_output=True,
                                text=True,
                            ).stdout.splitlines()
                            if line.strip()
                        ]
                        base_tree_hash = (
                            subprocess.run(
                                ["git", "rev-parse", f"{head_before}^{{tree}}"],
                                cwd=target_dir,
                                env=env,
                                check=True,
                                capture_output=True,
                                text=True,
                            )
                            .stdout.strip()
                            .casefold()
                        )
                        subprocess.run(
                            ["git", "merge", "--abort"],
                            cwd=target_dir,
                            env=env,
                            check=False,
                            capture_output=True,
                            text=True,
                        )
                        raise WorkerDependencyCompositionConflict(
                            "dependency Git composition conflict for dependency "
                            f"{source['dependency_task_id']} deliverable "
                            f"{source['deliverable_id']}",
                            details={
                                "base_branch": base_ref,
                                "base_commit_sha": head_before,
                                "base_tree_hash": base_tree_hash,
                                "dependency_git_sources": expected_rows,
                                "failed_dependency_task_id": source["dependency_task_id"],
                                "failed_deliverable_id": source["deliverable_id"],
                                "conflict_paths": conflict_paths,
                            },
                        )
                result_tree = (
                    subprocess.run(
                        ["git", "rev-parse", "HEAD^{tree}"],
                        cwd=target_dir,
                        env=env,
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    .stdout.strip()
                    .casefold()
                )
                commit_command = ["git", "commit-tree", result_tree, "-p", head_before]
                for source in frontier_rows:
                    commit_command.extend(("-p", str(source["commit_sha"])))
                commit_command.extend(("-m", dependency_composition_commit_message))
                parent_timestamps = [
                    int(
                        subprocess.run(
                            ["git", "show", "-s", "--format=%ct", parent],
                            cwd=target_dir,
                            env=env,
                            check=True,
                            capture_output=True,
                            text=True,
                        ).stdout.strip()
                    )
                    for parent in [
                        head_before,
                        *(str(source["commit_sha"]) for source in frontier_rows),
                    ]
                ]
                deterministic_date = f"@{max(parent_timestamps) + 1} +0000"
                commit_env = {
                    **composition_env,
                    "GIT_AUTHOR_DATE": deterministic_date,
                    "GIT_COMMITTER_DATE": deterministic_date,
                }
                composed_commit = (
                    subprocess.run(
                        commit_command,
                        cwd=target_dir,
                        env=commit_env,
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    .stdout.strip()
                    .casefold()
                )
                subprocess.run(
                    ["git", "reset", "--hard", composed_commit],
                    cwd=target_dir,
                    env=env,
                    check=True,
                    capture_output=True,
                    text=True,
                )
        except Exception:
            subprocess.run(
                ["git", "reset", "--hard", head_before],
                cwd=target_dir,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            raise

        composed_base = (
            subprocess.run(
                ["git", "rev-parse", "HEAD^{commit}"],
                cwd=target_dir,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            .stdout.strip()
            .casefold()
        )
        for key, value in (
            ("evander.dependencyGitProvenance", expected_json),
            ("evander.baseCommit", composed_base),
        ):
            subprocess.run(
                ["git", "config", "--worktree", key, value],
                cwd=target_dir,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
        return composed_base

    def cleanup_task_worktree(
        self,
        session_id: str,
        request: WorkerTaskWorktreeCleanupRequest,
    ) -> WorkerTaskWorktreeCleanupRecord:
        paths = WorkerSessionPaths.resolve(self.worker_root, session_id)
        repo_dir = paths.repo_root / request.repo_name
        ensure_resolved_path_within(
            base=paths.repo_root,
            candidate=repo_dir,
            description="task worktree repository",
        )
        worktrees_root = paths.agent_home / "worktrees" / request.repo_name
        target_dir = worktrees_root / request.worktree_name
        ensure_resolved_path_within(
            base=paths.agent_home,
            candidate=target_dir,
            description="task worktree target",
        )
        expected_target_dir = f"/home/agent/worktrees/{request.repo_name}/{request.worktree_name}"
        if request.expected_target_dir != expected_target_dir:
            raise ValueError(
                "expected_target_dir does not match the deterministic task worktree path"
            )
        expected_commit_sha = request.expected_commit_sha
        if expected_commit_sha is None:
            receipt = self._validated_task_worktree_preparation_receipt(paths, request)
            if receipt is None:
                raise ValueError(
                    "task worktree cleanup requires its preparation receipt when "
                    "expected_commit_sha is omitted"
                )
            expected_commit_sha = str(receipt["commit_sha"]).strip().casefold()
        lock_path = self._task_worktree_lock_path(paths, request.repo_name)
        outcome = cleanup_task_worktree_checkout(
            trusted_root=paths.agent_home,
            repo_dir=repo_dir,
            worktrees_root=worktrees_root,
            target_dir=target_dir,
            branch=request.branch,
            expected_commit_sha=expected_commit_sha,
            lock_path=lock_path,
            dry_run=request.dry_run,
        )
        if (
            request.preparation_attempt_id is not None
            and not request.dry_run
            and outcome.state in {"removed", "already_absent"}
        ):
            with task_worktree_repository_lock(lock_path):
                if target_dir.exists():
                    raise ValueError(
                        "task worktree reappeared before its preparation receipt was retired"
                    )
                self._delete_task_worktree_preparation_receipt(paths, request)
        return WorkerTaskWorktreeCleanupRecord(
            session_id=paths.session_id,
            repo_name=request.repo_name,
            worktree_name=request.worktree_name,
            target_dir=expected_target_dir,
            branch=request.branch,
            expected_commit_sha=expected_commit_sha,
            actual_branch=outcome.actual_branch,
            actual_commit_sha=outcome.actual_commit_sha,
            state=outcome.state,
            safe_to_remove=outcome.safe_to_remove,
            removed=outcome.removed,
            pruned=outcome.pruned,
            dry_run=request.dry_run,
            validation_count=outcome.validation_count,
            failed_check=outcome.failed_check,
            message=outcome.message,
            tracked_paths=outcome.tracked_paths,
            untracked_paths=outcome.untracked_paths,
            ignored_paths=outcome.ignored_paths,
            ignored_bytes=outcome.ignored_bytes,
            worktree_bytes=outcome.worktree_bytes,
            reclaimed_bytes=outcome.reclaimed_bytes,
        )
