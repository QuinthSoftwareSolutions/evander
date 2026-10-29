from __future__ import annotations

from collections.abc import Mapping
import ipaddress
from pathlib import Path
import posixpath
import shlex
from typing import Any
from urllib.parse import unquote, urlparse, urlunparse

from evander.execution import ExecutionBackendKind, ExecutionSpec, ExecutionStatusKind
from evander.events.provider_models import AgentCloneRepoEffectDefinition
from evander.git_pr_notes import agent_pr_notes_ref_for_session
from evander.git_identity import DEFAULT_AGENT_GIT_IDENTITY, GitIdentity
from evander.git_ssh_authorized_keys import (
    authorized_keys_path_for_user,
    install_git_read_authorized_key,
    install_git_write_authorized_key,
    remove_git_authorized_key_by_public_key_body,
)
from evander.managed_git_grants import (
    managed_git_grants_from_metadata,
    managed_git_key_name,
    managed_git_ssh_alias,
    normalize_git_access_lifetime,
    public_managed_git_grant,
    remove_managed_git_grant_metadata,
    upsert_managed_git_grant_metadata,
)

from .agent_session import (
    container_shell_preamble,
    container_backend,
    container_worker_image_for_session,
    container_worker_resource_limits_for_session,
    effective_config_for_session,
    normalize_optional_string,
    require_container_session_id,
    required_string,
    resolve_vm_target,
    safe_effect_execution_id,
    selected_execution_backend_for_target,
    wait_for_container_execution,
)
from .base import EffectProviderBase


_DEFAULT_GIT_IDENTITY = DEFAULT_AGENT_GIT_IDENTITY


class AgentCloneRepoEffectProvider(EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        effect = self._effect()
        context = self._context()
        payload = {
            "target": effect.target,
            "session_id": effect.session_id,
            "vm_name": effect.vm_name,
            "managed_repo_id": effect.managed_repo_id,
            "clone_branch": effect.clone_branch,
            "vm_user": effect.vm_user,
            "git_author_name": effect.git_author_name,
            "git_author_email": effect.git_author_email,
            "git_committer_name": effect.git_committer_name,
            "git_committer_email": effect.git_committer_email,
            "git_access_mode": effect.git_access_mode,
            "git_access_lifetime": effect.git_access_lifetime,
            "pinned_ref": effect.pinned_ref,
            "pinned_commit_sha": effect.pinned_commit_sha,
        }
        if resolved_config:
            payload.update(dict(resolved_config))
        git_author_identity = _git_author_identity_from_payload(payload)
        git_committer_identity = _git_committer_identity_from_payload(payload)

        directory_path = str(context.metadata.get("directory_path") or "root")
        execution_backend, config = selected_execution_backend_for_target(
            context, payload, directory_path=directory_path
        )
        if execution_backend == "libvirt_vm":
            result = self._execute_libvirt(
                payload,
                execution_backend=execution_backend,
                git_author_identity=git_author_identity,
                git_committer_identity=git_committer_identity,
            )
            return self._with_submission_checkout(result, payload)
        if execution_backend != "container_worker_vm":
            raise ValueError(f"Unsupported execution_backend: {execution_backend}")

        session_id, session = require_container_session_id(
            context,
            payload,
            effect_type="effect:agent:clone-repo",
            config=config,
        )
        session_directory_path = str(getattr(session, "directory_path", None) or directory_path)
        config = effective_config_for_session(
            context,
            session_id,
            fallback_directory_path=session_directory_path,
        )
        managed_repo_id = required_string(
            payload.get("managed_repo_id"),
            "effect:agent:clone-repo requires managed_repo_id",
        )
        clone_branch = normalize_optional_string(payload.get("clone_branch"))
        pinned_ref = normalize_optional_string(payload.get("pinned_ref"))
        pinned_commit_sha = normalize_optional_string(payload.get("pinned_commit_sha"))
        clone_resolver = getattr(context.admin_service, "resolve_managed_git_clone", None)
        if not callable(clone_resolver):
            raise RuntimeError("managed Git clone resolver is not available")
        clone_info = clone_resolver(
            managed_repo_id=managed_repo_id,
            directory_path=session_directory_path,
            session_id=session_id,
        )
        if hasattr(clone_info, "model_dump"):
            clone_info = clone_info.model_dump(mode="python")
        if not isinstance(clone_info, Mapping):
            raise RuntimeError("managed Git clone resolver returned an invalid response")
        clone_url = required_string(
            clone_info.get("clone_url") or clone_info.get("worker_repo_source"),
            "managed Git clone URL is unavailable",
        )
        repo_name = required_string(
            clone_info.get("repo_name") or clone_info.get("display_name") or managed_repo_id,
            "managed Git display name is unavailable",
        )
        backend = container_backend(context, config)
        backend.prepare_session(
            session_id=session_id,
            metadata={
                "execution_backend": "container_worker_vm",
                "directory_path": session_directory_path,
            },
        )
        container_job_timeout_seconds = float(config.container_worker_job_timeout_seconds)
        git_access_mode = normalize_git_access_mode(payload.get("git_access_mode"))
        git_access_lifetime = normalize_git_access_lifetime(payload.get("git_access_lifetime"))
        access_metadata: dict[str, object] | None = None
        clone_source = clone_url
        if _remote_url_requires_git_ssh(clone_url):
            managed_git = getattr(context.admin_service, "managed_git_admin", None)
            resolve_internal = getattr(managed_git, "resolve_internal_repository", None)
            if not callable(resolve_internal):
                raise RuntimeError("managed Git internal repository resolver is unavailable")
            bare_repo_path, managed_git_root = resolve_internal(
                managed_repo_id,
                session_directory_path,
            )
            access_metadata = self._provision_container_git_access(
                backend=backend,
                config=config,
                context=context,
                session_id=session_id,
                issue_setup=_container_git_connection_config(
                    context,
                    session_directory_path,
                ),
                managed_repo_id=managed_repo_id,
                bare_repo_path=bare_repo_path,
                managed_git_root=managed_git_root,
                access_mode=git_access_mode,
                lifetime=git_access_lifetime,
            )
            clone_source = _container_git_remote_url_with_ssh_alias(
                clone_url,
                str(access_metadata["git_ssh_alias"]),
            )
        try:
            result = backend.clone_repo(
                session_id=session_id,
                repo_source=clone_source,
                clone_branch=clone_branch,
                repo_name=repo_name,
                reconcile_existing_remote=bool(
                    isinstance(getattr(session, "metadata", None), Mapping)
                    and session.metadata.get("managed_git_remote_reconciliation_required")
                ),
            )
            self._write_container_git_helpers(
                backend=backend,
                session_id=session_id,
                target_dir=result.target_dir,
            )
            write_access_metadata = access_metadata if git_access_mode == "write" else None
            self._configure_container_git(
                backend=backend,
                config=config,
                session_id=session_id,
                session=session,
                directory_path=session_directory_path,
                target_dir=result.target_dir,
                repo_source=clone_source,
                remote_url=result.remote_url,
                push_remote_url=(
                    _container_git_remote_url_with_ssh_alias(
                        result.remote_url,
                        str(write_access_metadata["git_ssh_alias"]),
                    )
                    if write_access_metadata is not None
                    else None
                ),
                clone_branch=clone_branch,
                git_access_mode=git_access_mode,
                git_access_lifetime=git_access_lifetime,
                pinned_ref=pinned_ref,
                pinned_commit_sha=pinned_commit_sha,
                git_author_identity=git_author_identity,
                git_committer_identity=git_committer_identity,
                timeout_seconds=container_job_timeout_seconds,
            )
            bind_repository = getattr(context.admin_service, "bind_managed_git_repository", None)
            if callable(bind_repository):
                bind_repository(
                    managed_repo_id=managed_repo_id,
                    kind="sessions",
                    binding_id=session_id,
                )
            if access_metadata is not None and git_access_lifetime == "operation":
                cleanup_failures = self._rollback_container_git_access(
                    backend=backend,
                    config=config,
                    context=context,
                    session=session,
                    session_id=session_id,
                    directory_path=session_directory_path,
                    access_metadata=access_metadata,
                    timeout_seconds=container_job_timeout_seconds,
                )
                if cleanup_failures:
                    raise RuntimeError(
                        "Temporary container Git access cleanup was incomplete: "
                        + ", ".join(cleanup_failures)
                    )
        except BaseException as exc:
            if access_metadata is not None:
                try:
                    cleanup_failures = self._rollback_container_git_access(
                        backend=backend,
                        config=config,
                        context=context,
                        session=session,
                        session_id=session_id,
                        directory_path=session_directory_path,
                        access_metadata=access_metadata,
                        timeout_seconds=container_job_timeout_seconds,
                    )
                except Exception:
                    cleanup_failures = ["rollback failed unexpectedly"]
                if cleanup_failures:
                    exc.add_note(
                        "Container Git access rollback was incomplete: "
                        + ", ".join(cleanup_failures)
                    )
            raise
        result = {
            "success": True,
            "message": (
                f"Cloned repo into container worker session {session_id}"
                if result.cloned
                else f"Repo already prepared for container worker session {session_id}"
            ),
            "session_id": session_id,
            "target_dir": result.target_dir,
            "guest_repo_name": result.guest_repo_name,
            # Older worker implementations and compatible test doubles may not
            # return a commit snapshot. Keep clone responses backwards-compatible
            # while exposing the immutable commit whenever the worker provides it.
            "commit_sha": getattr(result, "commit_sha", None),
            "pinned_ref": pinned_ref,
            "pinned_commit_sha": pinned_commit_sha,
            "managed_repo_id": managed_repo_id,
            "pr_notes_ref": agent_pr_notes_ref_for_session(session_id),
            "git_access_mode": git_access_mode,
            "git_access_lifetime": git_access_lifetime,
            "git_access": _public_git_access_metadata(
                access_metadata if git_access_lifetime == "session" else None
            ),
            "git_write_access": _public_git_access_metadata(
                write_access_metadata if git_access_lifetime == "session" else None
            ),
            "execution_backend": "container_worker_vm",
            "vm_name": None,
        }
        return self._with_submission_checkout(result, payload)

    def _with_submission_checkout(self, result, payload):
        register = getattr(self._context().admin_service, "register_git_submission_checkout", None)
        if (
            callable(register)
            and result.get("success")
            and result.get("target_dir", "").startswith("/home/agent/")
            and normalize_git_access_mode(payload.get("git_access_mode")) == "write"
            and normalize_git_access_lifetime(payload.get("git_access_lifetime")) == "session"
        ):
            checkout_id = register(
                session_id=result["session_id"],
                managed_repo_id=result["managed_repo_id"],
                guest_path=result["target_dir"],
                repository_name=result.get("guest_repo_name"),
                base_branch=payload.get("clone_branch"),
            )
            if checkout_id:
                result["checkout_id"] = checkout_id
        return result

    def _rollback_container_git_access(
        self,
        *,
        backend: Any,
        config: Any,
        context: Any,
        session: Any,
        session_id: str,
        directory_path: str,
        access_metadata: Mapping[str, object],
        timeout_seconds: float,
    ) -> list[str]:
        failures: list[str] = []
        host_access_revoked = False
        authorized_keys_path = normalize_optional_string(
            access_metadata.get("authorized_keys_path")
        )
        public_key_body = normalize_optional_string(access_metadata.get("public_key_body"))
        if authorized_keys_path and public_key_body:
            try:
                # A successful call proves the key is absent afterwards. The boolean return
                # only distinguishes "removed" from "already absent".
                remove_git_authorized_key_by_public_key_body(
                    authorized_keys_path=authorized_keys_path,
                    public_key_body=public_key_body,
                )
            except Exception:
                failures.append("host key revocation failed")
            else:
                host_access_revoked = True
        else:
            failures.append("host key revocation metadata is incomplete")

        worker_key_files_removed = False
        try:
            self._remove_container_git_write_key_files(
                backend=backend,
                config=config,
                session=session,
                session_id=session_id,
                directory_path=directory_path,
                timeout_seconds=timeout_seconds,
                key_name=required_string(
                    access_metadata.get("key_name"),
                    "container Git access key_name is missing",
                ),
            )
        except Exception:
            failures.append("worker key removal failed")
        else:
            worker_key_files_removed = True

        metadata_cleared = False
        if host_access_revoked and worker_key_files_removed and public_key_body:
            try:
                metadata_cleared = _clear_session_git_access_metadata(
                    context,
                    session_id,
                    managed_repo_id=required_string(
                        access_metadata.get("managed_repo_id"),
                        "container Git access managed_repo_id is missing",
                    ),
                    expected_public_key_body=public_key_body,
                )
            except Exception:
                failures.append("session Git access metadata cleanup failed")
            if not metadata_cleared and not any(
                failure == "session Git access metadata cleanup failed" for failure in failures
            ):
                failures.append("session Git access metadata changed during cleanup")

        if host_access_revoked and worker_key_files_removed and metadata_cleared:
            unbind = getattr(context.admin_service, "unbind_managed_git_repository", None)
            if callable(unbind):
                try:
                    unbind(
                        managed_repo_id=required_string(
                            access_metadata.get("managed_repo_id"),
                            "container Git access managed_repo_id is missing",
                        ),
                        kind="authorized_sessions",
                        binding_id=session_id,
                    )
                except Exception:
                    failures.append("authorized session binding cleanup failed")
            else:
                failures.append("authorized session binding cleanup is unavailable")
        return failures

    def _write_container_git_helpers(
        self,
        *,
        backend: Any,
        session_id: str,
        target_dir: str,
    ) -> None:
        backend.write_files(
            session_id=session_id,
            files=[
                {
                    "destination_path": (f"{target_dir.rstrip('/')}/.evander/bin/agent-pr-note"),
                    "content": self._agent_pr_note_helper_content(),
                    "mode": "0755",
                },
                {
                    "destination_path": f"{target_dir.rstrip('/')}/.evander/bin/push-agent",
                    "content": self._push_agent_helper_content(),
                    "mode": "0755",
                },
                {
                    "destination_path": f"{target_dir.rstrip('/')}/.evander/bin/sync-base",
                    "content": self._sync_base_helper_content(),
                    "mode": "0755",
                },
            ],
        )

    def _provision_container_git_access(
        self,
        *,
        backend: Any,
        config: Any,
        context: Any,
        session_id: str,
        issue_setup: Any,
        managed_repo_id: str,
        bare_repo_path: Path,
        managed_git_root: Path,
        access_mode: str,
        lifetime: str,
    ) -> dict[str, object]:
        provision_git_write_key = getattr(backend, "provision_git_write_key", None)
        if not callable(provision_git_write_key):
            raise RuntimeError("container worker backend does not support session Git write keys")
        _remove_existing_repo_host_authorization(context, session_id, managed_repo_id)
        key_name = managed_git_key_name(managed_repo_id)
        git_ssh_alias = managed_git_ssh_alias(issue_setup.git_ssh_alias, managed_repo_id)
        key_record = provision_git_write_key(
            session_id=session_id,
            git_host=issue_setup.git_host,
            git_user=issue_setup.git_user,
            git_ssh_alias=git_ssh_alias,
            key_name=key_name,
            alias_is_exact=True,
        )
        project_dir = Path(getattr(context.admin_service, "project_dir", Path.cwd()))
        allowed_from = container_worker_git_allowed_from(config)
        authorized_keys_path = authorized_keys_path_for_user(issue_setup.git_user)
        common_install_args = {
            "public_key": str(key_record.public_key),
            "git_root": managed_git_root,
            "authorized_repo_path": bare_repo_path,
            "allowed_from": allowed_from,
            "wrapper_path": project_dir / "scripts" / "git_ssh_command_wrapper.sh",
            "authorized_keys_path": authorized_keys_path,
        }
        if access_mode == "write":
            installed = install_git_write_authorized_key(
                **common_install_args,
                session_id=session_id,
            )
        else:
            installed = install_git_read_authorized_key(**common_install_args)
        metadata = {
            "public_key_body": installed.public_key_body,
            "managed_repo_id": managed_repo_id,
            "git_user": issue_setup.git_user,
            "authorized_keys_path": str(installed.authorized_keys_path),
            "access_mode": access_mode,
            "lifetime": lifetime,
            "execution_backend": "container_worker_vm",
            "allowed_from": installed.allowed_from,
            "git_ssh_alias": str(key_record.git_ssh_alias),
            "key_name": key_name,
        }
        if access_mode == "write":
            metadata.update(
                {
                    "authorized_ref_prefix": installed.authorized_ref_prefix,
                    "authorized_pr_notes_ref": installed.authorized_pr_notes_ref,
                }
            )
        try:
            _record_session_git_access(context, session_id, metadata)
            bind_repository = getattr(context.admin_service, "bind_managed_git_repository", None)
            if callable(bind_repository):
                bind_repository(
                    managed_repo_id=managed_repo_id,
                    kind="authorized_sessions",
                    binding_id=session_id,
                )
        except Exception:
            remove_git_authorized_key_by_public_key_body(
                authorized_keys_path=installed.authorized_keys_path,
                public_key_body=installed.public_key_body,
            )
            raise
        return metadata

    def _remove_container_git_write_key_files(
        self,
        *,
        backend: Any,
        config: Any,
        session: Any,
        session_id: str,
        directory_path: str,
        timeout_seconds: float,
        key_name: str,
    ) -> None:
        revoke_git_key = getattr(backend, "revoke_git_key", None)
        if callable(revoke_git_key):
            revoke_git_key(session_id=session_id, key_name=key_name)
            return
        execution_id = safe_effect_execution_id("clone-git-cleanup", session_id)
        command = (
            container_shell_preamble().rstrip()
            + "\n"
            + f'rm -f "$HOME/.ssh/{shlex.quote(key_name)}" '
            + f'"$HOME/.ssh/{shlex.quote(key_name)}.pub"\n'
        )
        handle = backend.start(
            backend.prepare(
                ExecutionSpec(
                    backend=ExecutionBackendKind.CONTAINER_WORKER_VM,
                    target_name=execution_id,
                    session_id=session_id,
                    directory_path=directory_path,
                    image=container_worker_image_for_session(session, config),
                    resource_limits=container_worker_resource_limits_for_session(session),
                    command=command,
                    metadata={
                        "effect_type": "effect:agent:clone-repo:git-write-cleanup",
                    },
                )
            )
        )
        logs = ""
        status = None
        try:
            status = wait_for_container_execution(
                backend,
                handle.execution_id,
                timeout_seconds=timeout_seconds,
            )
            try:
                logs = str(backend.get_log_snapshot(handle.execution_id, lines=200) or "")
            except Exception:
                logs = ""
        finally:
            try:
                backend.destroy(handle.execution_id)
            except Exception:
                pass
        if status is None or status.status != ExecutionStatusKind.STOPPED:
            message = (
                normalize_optional_string(getattr(status, "message", None)) if status else None
            )
            raise RuntimeError(
                message
                or normalize_optional_string(logs)
                or "Failed to remove stale container worker Git write key"
            )

    def _configure_container_git(
        self,
        *,
        backend: Any,
        config: Any,
        session: Any,
        session_id: str,
        directory_path: str,
        target_dir: str,
        repo_source: str,
        remote_url: str,
        push_remote_url: str | None,
        clone_branch: str | None,
        git_access_mode: str,
        git_access_lifetime: str,
        pinned_ref: str | None,
        pinned_commit_sha: str | None,
        git_author_identity: GitIdentity | None,
        git_committer_identity: GitIdentity | None,
        timeout_seconds: float,
    ) -> None:
        execution_id = safe_effect_execution_id("clone-config", session_id)
        command = self._container_git_config_command(
            session_id=session_id,
            target_dir=target_dir,
            repo_source=repo_source,
            remote_url=remote_url,
            push_remote_url=push_remote_url,
            clone_branch=clone_branch,
            git_access_mode=git_access_mode,
            git_access_lifetime=git_access_lifetime,
            pinned_ref=pinned_ref,
            pinned_commit_sha=pinned_commit_sha,
            git_author_identity=git_author_identity,
            git_committer_identity=git_committer_identity,
        )
        handle = backend.start(
            backend.prepare(
                ExecutionSpec(
                    backend=ExecutionBackendKind.CONTAINER_WORKER_VM,
                    target_name=execution_id,
                    session_id=session_id,
                    directory_path=directory_path,
                    image=container_worker_image_for_session(session, config),
                    resource_limits=container_worker_resource_limits_for_session(session),
                    command=command,
                    metadata={"effect_type": "effect:agent:clone-repo:git-config"},
                )
            )
        )
        logs = ""
        status = None
        try:
            status = wait_for_container_execution(
                backend,
                handle.execution_id,
                timeout_seconds=timeout_seconds,
            )
            try:
                logs = str(backend.get_log_snapshot(handle.execution_id, lines=200) or "")
            except Exception:
                logs = ""
        finally:
            try:
                backend.destroy(handle.execution_id)
            except Exception:
                pass
        if status is None or status.status != ExecutionStatusKind.STOPPED:
            message = (
                normalize_optional_string(getattr(status, "message", None)) if status else None
            )
            raise RuntimeError(
                message
                or normalize_optional_string(logs)
                or "Failed to configure git in container worker repo"
            )

    def _agent_pr_note_helper_content(self) -> str:
        return self._guest_helper_content("agent_pr_note.sh")

    def _push_agent_helper_content(self) -> str:
        return self._guest_helper_content("push_agent.sh")

    def _sync_base_helper_content(self) -> str:
        return self._guest_helper_content("sync_base.sh")

    def _guest_helper_content(self, helper_name: str) -> str:
        context = self._context()
        project_dir = Path(getattr(context.admin_service, "project_dir", Path.cwd()))
        helper_path = project_dir / "scripts" / "guest" / helper_name
        if not helper_path.exists():
            raise RuntimeError(f"Missing guest Git helper: {helper_path}")
        return helper_path.read_text(encoding="utf-8")

    @staticmethod
    def _container_git_config_command(
        *,
        session_id: str,
        target_dir: str,
        repo_source: str,
        remote_url: str,
        push_remote_url: str | None = None,
        clone_branch: str | None,
        git_access_mode: str = "write",
        git_access_lifetime: str = "session",
        pinned_ref: str | None = None,
        pinned_commit_sha: str | None = None,
        git_identity: GitIdentity | None = None,
        git_author_identity: GitIdentity | None = None,
        git_committer_identity: GitIdentity | None = None,
    ) -> str:
        if git_author_identity is None and git_identity is not None:
            git_author_identity = git_identity
        canonical_remote_url = repo_source.strip() if _is_remote_git_source(repo_source) else ""
        normalized_git_access_mode = normalize_git_access_mode(git_access_mode)
        normalized_git_access_lifetime = normalize_git_access_lifetime(git_access_lifetime)
        if normalized_git_access_mode != "write":
            push_remote_url = None
        elif push_remote_url is None:
            push_remote_url = _container_reachable_git_remote_url(remote_url)
        pr_notes_ref = agent_pr_notes_ref_for_session(session_id)
        git_user_identity = git_author_identity or _DEFAULT_GIT_IDENTITY
        lines = [
            container_shell_preamble().rstrip(),
            f"cd {shlex.quote(target_dir)}",
            f"REMOTE_URL={shlex.quote(remote_url)}",
            f"PUSH_REMOTE_URL={shlex.quote(push_remote_url or '')}",
            f"CANONICAL_REMOTE_URL={shlex.quote(canonical_remote_url)}",
            f"SESSION_ID={shlex.quote(session_id)}",
            f"BRANCH_SUFFIX={shlex.quote(session_id)}",
            f"PR_NOTES_REF={shlex.quote(pr_notes_ref)}",
            f"GIT_ACCESS_MODE={shlex.quote(normalized_git_access_mode)}",
            f"GIT_ACCESS_LIFETIME={shlex.quote(normalized_git_access_lifetime)}",
            f"PINNED_REF={shlex.quote(pinned_ref or '')}",
            f"PINNED_COMMIT_SHA={shlex.quote(pinned_commit_sha or '')}",
            "WORK_BRANCH=work",
            'if [ -n "$PINNED_REF" ]; then',
            '  git fetch --no-tags origin "$PINNED_REF"',
            '  test "$(git rev-parse FETCH_HEAD)" = "$PINNED_COMMIT_SHA"',
            "  git checkout --detach FETCH_HEAD",
            '  test "$(git rev-parse HEAD)" = "$PINNED_COMMIT_SHA"',
            "fi",
            'if [ -n "$PUSH_REMOTE_URL" ]; then',
            '  if git remote get-url agent >/dev/null 2>&1; then git remote set-url agent "$PUSH_REMOTE_URL"; else git remote add agent "$PUSH_REMOTE_URL"; fi',
            '  if [ -n "$CANONICAL_REMOTE_URL" ] && [ "$CANONICAL_REMOTE_URL" != "$PUSH_REMOTE_URL" ]; then',
            '    git remote set-url origin "$CANONICAL_REMOTE_URL"',
            '    git config "url.${PUSH_REMOTE_URL}.insteadOf" "$CANONICAL_REMOTE_URL"',
            "  fi",
            '  git config evander.pushAgentAvailable "true"',
            "else",
            "  git remote remove agent >/dev/null 2>&1 || true",
            "  git config --unset-all alias.push-agent >/dev/null 2>&1 || true",
            '  git config evander.pushAgentAvailable "false"',
            "fi",
            f"git config user.name {shlex.quote(git_user_identity.name)}",
            f"git config user.email {shlex.quote(git_user_identity.email)}",
            "BASE_BRANCH=${CLONE_BRANCH:-}",
            'if [ -z "$BASE_BRANCH" ]; then BASE_BRANCH=$(git branch --show-current || true); fi',
            'if [ -n "$BASE_BRANCH" ]; then git config evander.baseBranch "$BASE_BRANCH"; fi',
            'git config evander.workBranch "$WORK_BRANCH"',
            "CURRENT_BRANCH=$(git branch --show-current || true)",
            'if [ -z "$PINNED_REF" ] && [ "$CURRENT_BRANCH" != "$WORK_BRANCH" ]; then',
            '  if git show-ref --verify --quiet "refs/heads/$WORK_BRANCH"; then git switch "$WORK_BRANCH"; else git switch -c "$WORK_BRANCH"; fi',
            "fi",
            'git config evander.sessionId "$SESSION_ID"',
            'git config evander.branchSuffix "$BRANCH_SUFFIX"',
            'git config evander.prNotesRef "$PR_NOTES_REF"',
            'git config evander.gitAccessMode "$GIT_ACCESS_MODE"',
            'git config evander.gitAccessLifetime "$GIT_ACCESS_LIFETIME"',
        ]
        if clone_branch:
            lines.insert(9, f"CLONE_BRANCH={shlex.quote(clone_branch)}")
        if git_author_identity is not None:
            lines.extend(
                [
                    f"git config author.name {shlex.quote(git_author_identity.name)}",
                    f"git config author.email {shlex.quote(git_author_identity.email)}",
                    f"git config evander.gitAuthorName {shlex.quote(git_author_identity.name)}",
                    f"git config evander.gitAuthorEmail {shlex.quote(git_author_identity.email)}",
                ]
            )
        if git_committer_identity is not None:
            lines.extend(
                [
                    f"git config committer.name {shlex.quote(git_committer_identity.name)}",
                    f"git config committer.email {shlex.quote(git_committer_identity.email)}",
                    f"git config evander.gitCommitterName {shlex.quote(git_committer_identity.name)}",
                    f"git config evander.gitCommitterEmail {shlex.quote(git_committer_identity.email)}",
                ]
            )
        lines.extend(
            [
                'git config alias.agent-pr-note \'!f(){ repo_root=$(git rev-parse --show-toplevel); "${repo_root}/.evander/bin/agent-pr-note" "$@"; }; f\'',
                'git config alias.sync-base \'!f(){ repo_root=$(git rev-parse --show-toplevel); "${repo_root}/.evander/bin/sync-base" "$@"; }; f\'',
            ]
        )
        if push_remote_url:
            lines.extend(
                [
                    'git config alias.push-agent \'!f(){ repo_root=$(git rev-parse --show-toplevel); "${repo_root}/.evander/bin/push-agent" "$@"; }; f\'',
                ]
            )
        return "\n".join(lines) + "\n"

    def _execute_libvirt(
        self,
        payload: dict[str, Any],
        *,
        execution_backend: str,
        git_author_identity: GitIdentity | None,
        git_committer_identity: GitIdentity | None,
    ) -> dict[str, Any]:
        context = self._context()
        repo_cloner = getattr(context.vm_repo_cloner, "clone_repo_into_vm", None)
        if not callable(repo_cloner):
            raise RuntimeError("effect:agent:clone-repo vm_repo_cloner is not available")
        vm_name, session_id = resolve_vm_target(
            context, payload, effect_type="effect:agent:clone-repo"
        )
        clone_kwargs: dict[str, Any] = {
            "vm_name": vm_name,
            "session_id": session_id,
            "managed_repo_id": required_string(
                payload.get("managed_repo_id"),
                "effect:agent:clone-repo requires managed_repo_id",
            ),
            "clone_branch": normalize_optional_string(payload.get("clone_branch")),
            "vm_user": normalize_optional_string(payload.get("vm_user")) or "agent",
            "directory_path": str(context.metadata.get("directory_path") or "root"),
            "git_author_name": git_author_identity.name if git_author_identity is not None else None,
            "git_author_email": git_author_identity.email if git_author_identity is not None else None,
            "git_committer_name": (
                git_committer_identity.name if git_committer_identity is not None else None
            ),
            "git_committer_email": (
                git_committer_identity.email if git_committer_identity is not None else None
            ),
            "git_access_mode": normalize_git_access_mode(payload.get("git_access_mode")),
            "git_access_lifetime": normalize_git_access_lifetime(payload.get("git_access_lifetime")),
        }
        pinned_ref = normalize_optional_string(payload.get("pinned_ref"))
        pinned_commit_sha = normalize_optional_string(payload.get("pinned_commit_sha"))
        if pinned_ref is not None or pinned_commit_sha is not None:
            clone_kwargs["pinned_ref"] = pinned_ref
            clone_kwargs["pinned_commit_sha"] = pinned_commit_sha
        result = repo_cloner(
            **clone_kwargs,
        )
        return {
            "success": bool(result.success),
            "message": normalize_optional_string(getattr(result, "message", None)) or "",
            "session_id": session_id,
            "target_dir": normalize_optional_string(getattr(result, "target_dir", None)),
            "guest_repo_name": normalize_optional_string(getattr(result, "guest_repo_name", None)),
            "managed_repo_id": required_string(
                payload.get("managed_repo_id"),
                "effect:agent:clone-repo requires managed_repo_id",
            ),
            "pr_notes_ref": agent_pr_notes_ref_for_session(session_id),
            "git_access_mode": normalize_git_access_mode(payload.get("git_access_mode")),
            "git_access_lifetime": normalize_git_access_lifetime(
                payload.get("git_access_lifetime")
            ),
            "pinned_ref": normalize_optional_string(payload.get("pinned_ref")),
            "pinned_commit_sha": normalize_optional_string(payload.get("pinned_commit_sha")),
            "execution_backend": execution_backend,
            "vm_name": result.vm_name or vm_name,
        }

    def _effect(self) -> AgentCloneRepoEffectDefinition:
        if not isinstance(self.effect_definition, AgentCloneRepoEffectDefinition):
            raise RuntimeError(
                "AgentCloneRepoEffectProvider initialized with a non-agent-clone-repo effect definition"
            )
        return self.effect_definition

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("AgentCloneRepoEffectProvider has no runtime context")
        return self.runtime_context


def _is_remote_git_source(value: str) -> bool:
    normalized = value.strip()
    return "://" in normalized or normalized.startswith("git@") or normalized.startswith("ssh.")


def _container_reachable_git_remote_url(value: str) -> str | None:
    normalized = value.strip()
    if not normalized:
        return None
    parsed = urlparse(normalized)
    if parsed.scheme == "ssh" and parsed.netloc and parsed.path:
        return normalized
    path = _container_mounted_git_path(normalized)
    if path is None:
        return None
    if not _is_container_mounted_path(path):
        return None
    return path


def _container_git_remote_url_with_ssh_alias(remote_url: str, git_ssh_alias: str) -> str:
    """Route a container SSH Git remote through its dedicated write-key alias."""

    parsed = urlparse(remote_url.strip())
    if parsed.scheme != "ssh" or not parsed.netloc or not parsed.path:
        return remote_url
    netloc = git_ssh_alias
    if parsed.username:
        netloc = f"{parsed.username}@{netloc}"
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    return urlunparse(parsed._replace(netloc=netloc))


def _container_mounted_git_path(remote_url: str) -> str | None:
    if remote_url.startswith("/"):
        return posixpath.normpath(remote_url)
    parsed = urlparse(remote_url)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        return None
    return posixpath.normpath(unquote(parsed.path))


def _is_container_mounted_path(path: str) -> bool:
    for root in ("/home/agent", "/artifacts"):
        if path == root or path.startswith(f"{root}/"):
            return True
    return False


def _container_git_connection_config(context: Any, directory_path: str) -> Any:
    admin_service = getattr(context, "admin_service", None)
    project_dir = Path(getattr(admin_service, "project_dir", Path.cwd()))
    issue_setup = None
    resolve_issue_setup = getattr(admin_service, "_resolve_issue_setup", None)
    if callable(resolve_issue_setup):
        issue_setup = resolve_issue_setup(directory_path, root_dir=project_dir)
    else:
        runtime = getattr(admin_service, "runtime", None)
        config = getattr(admin_service, "config", None)
        if runtime is not None and config is not None:
            from evander.admin.services.issue_setup_helpers import (
                resolve_effective_issue_setup_config,
            )

            issue_setup = resolve_effective_issue_setup_config(
                config=config,
                runtime=runtime,
                directory_path=directory_path,
                root_dir=project_dir,
            )
    if issue_setup is None:
        raise RuntimeError(
            "effect:agent:clone-repo with container_worker_vm requires issue setup config"
        )
    return issue_setup


def _public_git_access_metadata(
    metadata: Mapping[str, object] | None,
) -> dict[str, object] | None:
    if metadata is None:
        return None
    return public_managed_git_grant(metadata)


def normalize_git_access_mode(value: object) -> str:
    normalized = str(value or "write").strip().casefold().replace("_", "-")
    if normalized in {"", "write", "writable"}:
        return "write"
    if normalized in {"read-only", "readonly", "read"}:
        return "read-only"
    raise ValueError("effect:agent:clone-repo git_access_mode must be write or read-only")


def container_worker_git_allowed_from(config: Any) -> str:
    configured = normalize_optional_string(
        getattr(config, "container_worker_git_allowed_from", None)
    )
    if configured:
        return configured
    base_url = normalize_optional_string(getattr(config, "container_worker_base_url", None))
    if not base_url:
        raise ValueError(
            "container_worker_git_allowed_from or container_worker_base_url is required "
            "for container Git write access"
        )
    host = urlparse(base_url).hostname
    if not host:
        raise ValueError(
            f"Could not determine worker host from container_worker_base_url: {base_url}"
        )
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host
    suffix = 32 if address.version == 4 else 128
    return f"{address}/{suffix}"


def _remote_url_requires_git_ssh(remote_url: str) -> bool:
    normalized = str(remote_url or "").strip()
    if not normalized:
        return False
    parsed = urlparse(normalized)
    if parsed.scheme == "ssh":
        return True
    return bool(normalized.startswith("git@") or "@" in normalized.partition(":")[0])


def _record_session_git_access(
    context: Any,
    session_id: str,
    metadata: dict[str, object],
) -> None:
    update_target = getattr(context, "session_metadata_updater", None)
    update_fn = getattr(update_target, "session_update_metadata", None)
    update_error: Exception | None = None
    if callable(update_fn):
        try:
            # The session metadata updater performs a deep merge, so send the
            # repository-keyed entry instead of replacing unrelated grants.
            update_fn(
                target=session_id,
                metadata={
                    "managed_git_access_grants": {
                        str(metadata["managed_repo_id"]): metadata,
                    }
                },
            )
            return
        except Exception as exc:
            update_error = exc
    runtime = getattr(getattr(context, "admin_service", None), "runtime", None)
    repository = getattr(runtime, "session_repository", None)
    get_session = getattr(repository, "get_session", None)
    upsert_session = getattr(repository, "upsert_session", None)
    if not callable(get_session) or not callable(upsert_session):
        raise RuntimeError("Could not record container Git write access metadata") from update_error
    session = get_session(session_id)
    if session is None:
        raise RuntimeError(
            f"Could not record container Git write access metadata for missing session: {session_id}"
        ) from update_error
    current_metadata = getattr(session, "metadata", {})
    if not isinstance(current_metadata, dict):
        current_metadata = {}
    updated_metadata = upsert_managed_git_grant_metadata(current_metadata, metadata)
    upsert_session(session.model_copy(update={"metadata": updated_metadata}))


def _clear_session_git_access_metadata(
    context: Any,
    session_id: str,
    *,
    managed_repo_id: str,
    expected_public_key_body: str,
) -> bool:
    runtime = getattr(getattr(context, "admin_service", None), "runtime", None)
    repository = getattr(runtime, "session_repository", None)
    get_session = getattr(repository, "get_session", None)
    upsert_session = getattr(repository, "upsert_session", None)
    if not callable(get_session) or not callable(upsert_session):
        return False
    session = get_session(session_id)
    if session is None:
        return True
    current_metadata = getattr(session, "metadata", {})
    if not isinstance(current_metadata, dict):
        return True
    updated_metadata, removed = remove_managed_git_grant_metadata(
        current_metadata,
        managed_repo_id,
        expected_public_key_body=expected_public_key_body,
    )
    if not removed and managed_repo_id in managed_git_grants_from_metadata(current_metadata):
        return False
    upsert_session(session.model_copy(update={"metadata": updated_metadata}))
    return True


def _remove_session_git_write_access(
    context: Any,
    session_id: str,
    managed_repo_id: str | None = None,
) -> None:
    runtime = getattr(getattr(context, "admin_service", None), "runtime", None)
    repository = getattr(runtime, "session_repository", None)
    get_session = getattr(repository, "get_session", None)
    upsert_session = getattr(repository, "upsert_session", None)
    if not callable(get_session) or not callable(upsert_session):
        return
    session = get_session(session_id)
    if session is None:
        return
    current_metadata = getattr(session, "metadata", {})
    if not isinstance(current_metadata, dict):
        return
    grants = managed_git_grants_from_metadata(current_metadata)
    selected = (
        {managed_repo_id: grants[managed_repo_id]}
        if managed_repo_id and managed_repo_id in grants
        else grants
    )
    updated_metadata = dict(current_metadata)
    unbind = getattr(context.admin_service, "unbind_managed_git_repository", None)
    for repo_id, git_access in selected.items():
        public_key_body = normalize_optional_string(git_access.get("public_key_body"))
        authorized_keys_path = normalize_optional_string(git_access.get("authorized_keys_path"))
        if public_key_body and authorized_keys_path:
            remove_git_authorized_key_by_public_key_body(
                authorized_keys_path=authorized_keys_path,
                public_key_body=public_key_body,
            )
        if callable(unbind):
            unbind(
                managed_repo_id=repo_id,
                kind="authorized_sessions",
                binding_id=session_id,
            )
        updated_metadata, _ = remove_managed_git_grant_metadata(updated_metadata, repo_id)
    upsert_session(session.model_copy(update={"metadata": updated_metadata}))


def _remove_existing_repo_host_authorization(
    context: Any,
    session_id: str,
    managed_repo_id: str,
) -> None:
    runtime = getattr(getattr(context, "admin_service", None), "runtime", None)
    repository = getattr(runtime, "session_repository", None)
    get_session = getattr(repository, "get_session", None)
    if not callable(get_session):
        return
    session = get_session(session_id)
    metadata = getattr(session, "metadata", {}) if session is not None else {}
    grant = managed_git_grants_from_metadata(metadata).get(managed_repo_id)
    if not isinstance(grant, Mapping):
        return
    public_key_body = normalize_optional_string(grant.get("public_key_body"))
    authorized_keys_path = normalize_optional_string(grant.get("authorized_keys_path"))
    git_user = normalize_optional_string(grant.get("git_user"))
    if not authorized_keys_path and git_user:
        authorized_keys_path = str(authorized_keys_path_for_user(git_user))
    if public_key_body and authorized_keys_path:
        remove_git_authorized_key_by_public_key_body(
            authorized_keys_path=authorized_keys_path,
            public_key_body=public_key_body,
        )


def _git_author_identity_from_payload(payload: Mapping[str, Any]) -> GitIdentity | None:
    return _git_identity_from_payload(
        payload,
        name_key="git_author_name",
        email_key="git_author_email",
    )


def _git_committer_identity_from_payload(payload: Mapping[str, Any]) -> GitIdentity | None:
    return _git_identity_from_payload(
        payload,
        name_key="git_committer_name",
        email_key="git_committer_email",
    )


def _git_identity_from_payload(
    payload: Mapping[str, Any], *, name_key: str, email_key: str
) -> GitIdentity | None:
    name = normalize_optional_string(payload.get(name_key))
    email = normalize_optional_string(payload.get(email_key))
    if name is None and email is None:
        return None
    if name is None or email is None:
        raise ValueError(f"effect:agent:clone-repo {name_key} and {email_key} must both be set")
    return GitIdentity(name=name, email=email)
