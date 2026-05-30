from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import posixpath
import shlex
from typing import Any
from urllib.parse import unquote, urlparse

from agent_sandbox.execution import ExecutionBackendKind, ExecutionSpec, ExecutionStatusKind
from agent_sandbox.events.provider_models import AgentCloneRepoEffectDefinition
from agent_sandbox.git_pr_notes import agent_pr_notes_ref_for_session
from agent_sandbox.git_mirror import ContainerGitMirrorResolver

from .agent_session import (
    container_shell_preamble,
    container_backend,
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


class AgentCloneRepoEffectProvider(EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        effect = self._effect()
        context = self._context()
        payload = {
            "target": effect.target,
            "session_id": effect.session_id,
            "vm_name": effect.vm_name,
            "repo_source": effect.repo_source,
            "clone_branch": effect.clone_branch,
            "vm_user": effect.vm_user,
            "directory_path": effect.directory_path,
        }
        if resolved_config:
            payload.update(dict(resolved_config))

        directory_path = normalize_optional_string(payload.get("directory_path")) or str(
            context.metadata.get("directory_path") or "root"
        )
        execution_backend, config = selected_execution_backend_for_target(
            context, payload, directory_path=directory_path
        )
        if execution_backend == "libvirt_vm":
            return self._execute_libvirt(payload, execution_backend=execution_backend)
        if execution_backend != "container_worker_vm":
            raise ValueError(f"Unsupported execution_backend: {execution_backend}")

        session_id, session = require_container_session_id(
            context,
            payload,
            effect_type="effect:agent:clone-repo",
            config=config,
        )
        config = effective_config_for_session(
            context,
            session_id,
            fallback_directory_path=getattr(session, "directory_path", directory_path),
        )
        repo_source = required_string(
            payload.get("repo_source"), "effect:agent:clone-repo requires repo_source"
        )
        clone_branch = normalize_optional_string(payload.get("clone_branch"))
        mirror = _container_git_mirror_resolver(context, directory_path).resolve(
            repo_source=repo_source,
            clone_branch=clone_branch,
            directory_path=directory_path,
            session_id=session_id,
        )
        backend = container_backend(context, config)
        backend.prepare_session(
            session_id=session_id,
            metadata={
                "execution_backend": "container_worker_vm",
                "directory_path": session.directory_path if session is not None else directory_path,
            },
        )
        result = backend.clone_repo(
            session_id=session_id,
            repo_source=mirror.worker_repo_source,
            clone_branch=clone_branch,
            repo_name=mirror.repo_name,
        )
        self._write_container_git_helpers(
            backend=backend,
            session_id=session_id,
            target_dir=result.target_dir,
        )
        if result.cloned:
            self._configure_container_git(
                backend=backend,
                session_id=session_id,
                directory_path=session.directory_path if session is not None else directory_path,
                target_dir=result.target_dir,
                repo_source=mirror.canonical_repo_source,
                remote_url=result.remote_url,
                clone_branch=clone_branch,
                timeout_seconds=float(config.container_worker_job_timeout_seconds),
            )
        return {
            "success": True,
            "message": (
                f"Cloned repo into container worker session {session_id}"
                if result.cloned
                else f"Repo already prepared for container worker session {session_id}"
            ),
            "session_id": session_id,
            "target_dir": result.target_dir,
            "guest_repo_name": result.guest_repo_name,
            "remote_url": result.remote_url,
            "canonical_repo_source": mirror.canonical_repo_source,
            "pr_notes_ref": agent_pr_notes_ref_for_session(session_id),
            "execution_backend": "container_worker_vm",
            "vm_name": None,
        }

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
                    "destination_path": (
                        f"{target_dir.rstrip('/')}/.agent-sandbox/bin/agent-pr-note"
                    ),
                    "content": self._agent_pr_note_helper_content(),
                    "mode": "0755",
                },
                {
                    "destination_path": f"{target_dir.rstrip('/')}/.agent-sandbox/bin/push-agent",
                    "content": self._push_agent_helper_content(),
                    "mode": "0755",
                },
                {
                    "destination_path": f"{target_dir.rstrip('/')}/.agent-sandbox/bin/sync-base",
                    "content": self._sync_base_helper_content(),
                    "mode": "0755",
                },
            ],
        )

    def _configure_container_git(
        self,
        *,
        backend: Any,
        session_id: str,
        directory_path: str,
        target_dir: str,
        repo_source: str,
        remote_url: str,
        clone_branch: str | None,
        timeout_seconds: float,
    ) -> None:
        execution_id = safe_effect_execution_id("clone-config", session_id)
        command = self._container_git_config_command(
            session_id=session_id,
            target_dir=target_dir,
            repo_source=repo_source,
            remote_url=remote_url,
            clone_branch=clone_branch,
        )
        handle = backend.start(
            backend.prepare(
                ExecutionSpec(
                    backend=ExecutionBackendKind.CONTAINER_WORKER_VM,
                    target_name=execution_id,
                    session_id=session_id,
                    directory_path=directory_path,
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
            message = normalize_optional_string(getattr(status, "message", None)) if status else None
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
        clone_branch: str | None,
    ) -> str:
        canonical_remote_url = repo_source.strip() if _is_remote_git_source(repo_source) else ""
        push_remote_url = _container_reachable_git_remote_url(remote_url)
        pr_notes_ref = agent_pr_notes_ref_for_session(session_id)
        lines = [
            container_shell_preamble().rstrip(),
            f"cd {shlex.quote(target_dir)}",
            f"REMOTE_URL={shlex.quote(remote_url)}",
            f"PUSH_REMOTE_URL={shlex.quote(push_remote_url or '')}",
            f"CANONICAL_REMOTE_URL={shlex.quote(canonical_remote_url)}",
            f"SESSION_ID={shlex.quote(session_id)}",
            f"BRANCH_SUFFIX={shlex.quote(session_id)}",
            f"PR_NOTES_REF={shlex.quote(pr_notes_ref)}",
            "WORK_BRANCH=work",
            'if [ -n "$PUSH_REMOTE_URL" ]; then',
            '  if git remote get-url agent >/dev/null 2>&1; then git remote set-url agent "$PUSH_REMOTE_URL"; else git remote add agent "$PUSH_REMOTE_URL"; fi',
            '  if [ -n "$CANONICAL_REMOTE_URL" ] && [ "$CANONICAL_REMOTE_URL" != "$PUSH_REMOTE_URL" ]; then',
            '    git remote set-url origin "$CANONICAL_REMOTE_URL"',
            '    git config "url.${PUSH_REMOTE_URL}.insteadOf" "$CANONICAL_REMOTE_URL"',
            "  fi",
            '  git config agent-sandbox.pushAgentAvailable "true"',
            "else",
            '  git remote remove agent >/dev/null 2>&1 || true',
            '  git config --unset-all alias.push-agent >/dev/null 2>&1 || true',
            '  git config agent-sandbox.pushAgentAvailable "false"',
            "fi",
            'git config user.name "Truman Bot"',
            'git config user.email "truman@quinth.se"',
            'BASE_BRANCH=${CLONE_BRANCH:-}',
            'if [ -z "$BASE_BRANCH" ]; then BASE_BRANCH=$(git branch --show-current || true); fi',
            'if [ -n "$BASE_BRANCH" ]; then git config agent-sandbox.baseBranch "$BASE_BRANCH"; fi',
            'git config agent-sandbox.workBranch "$WORK_BRANCH"',
            'CURRENT_BRANCH=$(git branch --show-current || true)',
            'if [ "$CURRENT_BRANCH" != "$WORK_BRANCH" ]; then',
            '  if git show-ref --verify --quiet "refs/heads/$WORK_BRANCH"; then git switch "$WORK_BRANCH"; else git switch -c "$WORK_BRANCH"; fi',
            "fi",
            'git config agent-sandbox.sessionId "$SESSION_ID"',
            'git config agent-sandbox.branchSuffix "$BRANCH_SUFFIX"',
            'git config agent-sandbox.prNotesRef "$PR_NOTES_REF"',
        ]
        if clone_branch:
            lines.insert(8, f"CLONE_BRANCH={shlex.quote(clone_branch)}")
        lines.extend(
            [
                'git config alias.agent-pr-note \'!f(){ repo_root=$(git rev-parse --show-toplevel); "${repo_root}/.agent-sandbox/bin/agent-pr-note" "$@"; }; f\'',
                'git config alias.sync-base \'!f(){ repo_root=$(git rev-parse --show-toplevel); "${repo_root}/.agent-sandbox/bin/sync-base" "$@"; }; f\'',
            ]
        )
        if push_remote_url:
            lines.extend(
                [
                    'git config alias.push-agent \'!f(){ repo_root=$(git rev-parse --show-toplevel); "${repo_root}/.agent-sandbox/bin/push-agent" "$@"; }; f\'',
                ]
            )
        return "\n".join(lines) + "\n"

    def _execute_libvirt(self, payload: dict[str, Any], *, execution_backend: str) -> dict[str, Any]:
        context = self._context()
        repo_cloner = getattr(context.vm_repo_cloner, "clone_repo_into_vm", None)
        if not callable(repo_cloner):
            raise RuntimeError("effect:agent:clone-repo vm_repo_cloner is not available")
        vm_name, session_id = resolve_vm_target(
            context, payload, effect_type="effect:agent:clone-repo"
        )
        result = repo_cloner(
            vm_name=vm_name,
            session_id=session_id,
            repo_source=required_string(
                payload.get("repo_source"), "effect:agent:clone-repo requires repo_source"
            ),
            clone_branch=normalize_optional_string(payload.get("clone_branch")),
            vm_user=normalize_optional_string(payload.get("vm_user")) or "agent",
            directory_path=normalize_optional_string(payload.get("directory_path"))
            or str(context.metadata.get("directory_path") or "root"),
        )
        return {
            "success": bool(result.success),
            "message": normalize_optional_string(getattr(result, "message", None)) or "",
            "session_id": session_id,
            "target_dir": normalize_optional_string(getattr(result, "target_dir", None)),
            "guest_repo_name": normalize_optional_string(getattr(result, "guest_repo_name", None)),
            "remote_url": normalize_optional_string(getattr(result, "remote_url", None)),
            "pr_notes_ref": agent_pr_notes_ref_for_session(session_id),
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


def _container_git_mirror_resolver(context: Any, directory_path: str) -> ContainerGitMirrorResolver:
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
            from agent_sandbox.admin.service import resolve_effective_issue_setup_config

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
    return ContainerGitMirrorResolver(
        project_dir=project_dir,
        git_root=issue_setup.git_root,
        git_host=issue_setup.git_host,
        git_user=issue_setup.git_user,
        git_ssh_alias=issue_setup.git_ssh_alias,
    )
