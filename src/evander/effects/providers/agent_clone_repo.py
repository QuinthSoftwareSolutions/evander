from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import ipaddress
from pathlib import Path
import posixpath
import shlex
from typing import Any
from urllib.parse import unquote, urlparse

from evander.execution import ExecutionBackendKind, ExecutionSpec, ExecutionStatusKind
from evander.events.provider_models import AgentCloneRepoEffectDefinition
from evander.git_pr_notes import agent_pr_notes_ref_for_session
from evander.git_mirror import ContainerGitMirrorResolver
from evander.git_ssh_authorized_keys import (
    authorized_keys_path_for_user,
    install_git_write_authorized_key,
    remove_git_authorized_key_by_public_key_body,
)

from .agent_session import (
    container_shell_preamble,
    container_backend,
    container_worker_image_for_session,
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


@dataclass(frozen=True)
class GitIdentity:
    name: str
    email: str


GitAuthorIdentity = GitIdentity
_DEFAULT_GIT_IDENTITY = GitIdentity(name="Truman Bot", email="truman@quinth.se")


class AgentCloneRepoEffectProvider(EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        effect = self._effect()
        context = self._context()
        payload = {
            "target": effect.target,
            "session_id": effect.session_id,
            "vm_name": effect.vm_name,
            "repo_source": effect.repo_source,
            "canonical_origin_url": effect.canonical_origin_url,
            "bare_repo_name": effect.bare_repo_name,
            "bare_repo_path": effect.bare_repo_path,
            "clone_branch": effect.clone_branch,
            "vm_user": effect.vm_user,
            "directory_path": effect.directory_path,
            "git_author_name": effect.git_author_name,
            "git_author_email": effect.git_author_email,
            "git_committer_name": effect.git_committer_name,
            "git_committer_email": effect.git_committer_email,
            "git_access_mode": effect.git_access_mode,
        }
        if resolved_config:
            payload.update(dict(resolved_config))
        git_author_identity = _git_author_identity_from_payload(payload)
        git_committer_identity = _git_committer_identity_from_payload(payload)

        directory_path = normalize_optional_string(payload.get("directory_path")) or str(
            context.metadata.get("directory_path") or "root"
        )
        execution_backend, config = selected_execution_backend_for_target(
            context, payload, directory_path=directory_path
        )
        if execution_backend == "libvirt_vm":
            return self._execute_libvirt(
                payload,
                execution_backend=execution_backend,
                git_author_identity=git_author_identity,
                git_committer_identity=git_committer_identity,
            )
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
        mirror_resolver = _container_git_mirror_resolver(context, directory_path)
        mirror = mirror_resolver.resolve(
            repo_source=repo_source,
            canonical_repo_source=normalize_optional_string(payload.get("canonical_origin_url")),
            bare_repo_name=normalize_optional_string(payload.get("bare_repo_name")),
            bare_repo_path=normalize_optional_string(payload.get("bare_repo_path")),
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
        git_access_mode = normalize_git_access_mode(payload.get("git_access_mode"))
        if git_access_mode == "read-only":
            _remove_session_git_write_access(context, session_id)
            self._remove_container_git_write_key_files(
                backend=backend,
                config=config,
                session=session,
                session_id=session_id,
                directory_path=session.directory_path if session is not None else directory_path,
                timeout_seconds=float(config.container_worker_job_timeout_seconds),
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
        write_access_metadata: dict[str, object] | None = None
        if git_access_mode == "write" and _remote_url_requires_git_ssh(result.remote_url):
            write_access_metadata = self._provision_container_git_write_access(
                backend=backend,
                config=config,
                context=context,
                session_id=session_id,
                mirror_resolver=mirror_resolver,
                mirror_path=mirror.mirror_path,
            )
        if result.cloned or write_access_metadata is not None or git_access_mode == "read-only":
            self._configure_container_git(
                backend=backend,
                config=config,
                session_id=session_id,
                session=session,
                directory_path=session.directory_path if session is not None else directory_path,
                target_dir=result.target_dir,
                repo_source=mirror.canonical_repo_source,
                remote_url=result.remote_url,
                clone_branch=clone_branch,
                git_access_mode=git_access_mode,
                git_author_identity=git_author_identity,
                git_committer_identity=git_committer_identity,
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
            "git_access_mode": git_access_mode,
            "git_write_access": write_access_metadata,
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

    def _provision_container_git_write_access(
        self,
        *,
        backend: Any,
        config: Any,
        context: Any,
        session_id: str,
        mirror_resolver: ContainerGitMirrorResolver,
        mirror_path: Path,
    ) -> dict[str, object]:
        provision_git_write_key = getattr(backend, "provision_git_write_key", None)
        if not callable(provision_git_write_key):
            raise RuntimeError("container worker backend does not support session Git write keys")
        _remove_session_git_write_access(context, session_id)
        key_record = provision_git_write_key(
            session_id=session_id,
            git_host=mirror_resolver.git_host,
            git_user=mirror_resolver.git_user,
            git_ssh_alias=mirror_resolver.git_ssh_alias,
        )
        project_dir = Path(getattr(context.admin_service, "project_dir", Path.cwd()))
        allowed_from = container_worker_git_allowed_from(config)
        installed = install_git_write_authorized_key(
            public_key=str(key_record.public_key),
            git_root=mirror_resolver.git_root_path,
            authorized_repo_path=mirror_path,
            session_id=session_id,
            allowed_from=allowed_from,
            wrapper_path=project_dir / "scripts" / "git_ssh_command_wrapper.sh",
            authorized_keys_path=authorized_keys_path_for_user(mirror_resolver.git_user),
        )
        metadata = {
            "public_key_body": installed.public_key_body,
            "authorized_keys_path": str(installed.authorized_keys_path),
            "authorized_repo_path": str(installed.authorized_repo_path),
            "authorized_ref_prefix": installed.authorized_ref_prefix,
            "authorized_pr_notes_ref": installed.authorized_pr_notes_ref,
            "allowed_from": installed.allowed_from,
            "git_ssh_alias": mirror_resolver.git_ssh_alias,
        }
        _record_session_git_write_access(context, session_id, metadata)
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
    ) -> None:
        execution_id = safe_effect_execution_id("clone-git-cleanup", session_id)
        command = (
            container_shell_preamble().rstrip()
            + "\n"
            + 'rm -f "$HOME/.ssh/evander-git-write" '
            + '"$HOME/.ssh/evander-git-write.pub" "$HOME/.ssh/config"\n'
        )
        handle = backend.start(
            backend.prepare(
                ExecutionSpec(
                    backend=ExecutionBackendKind.CONTAINER_WORKER_VM,
                    target_name=execution_id,
                    session_id=session_id,
                    directory_path=directory_path,
                    image=container_worker_image_for_session(session, config),
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
        clone_branch: str | None,
        git_access_mode: str,
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
            clone_branch=clone_branch,
            git_access_mode=git_access_mode,
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
        clone_branch: str | None,
        git_access_mode: str = "write",
        git_identity: GitIdentity | None = None,
        git_author_identity: GitIdentity | None = None,
        git_committer_identity: GitIdentity | None = None,
    ) -> str:
        if git_author_identity is None and git_identity is not None:
            git_author_identity = git_identity
        canonical_remote_url = repo_source.strip() if _is_remote_git_source(repo_source) else ""
        normalized_git_access_mode = normalize_git_access_mode(git_access_mode)
        push_remote_url = (
            _container_reachable_git_remote_url(remote_url)
            if normalized_git_access_mode == "write"
            else None
        )
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
            "WORK_BRANCH=work",
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
            '  rm -f "$HOME/.ssh/evander-git-write" "$HOME/.ssh/evander-git-write.pub" "$HOME/.ssh/config"',
            "fi",
            f"git config user.name {shlex.quote(git_user_identity.name)}",
            f"git config user.email {shlex.quote(git_user_identity.email)}",
            "BASE_BRANCH=${CLONE_BRANCH:-}",
            'if [ -z "$BASE_BRANCH" ]; then BASE_BRANCH=$(git branch --show-current || true); fi',
            'if [ -n "$BASE_BRANCH" ]; then git config evander.baseBranch "$BASE_BRANCH"; fi',
            'git config evander.workBranch "$WORK_BRANCH"',
            "CURRENT_BRANCH=$(git branch --show-current || true)",
            'if [ "$CURRENT_BRANCH" != "$WORK_BRANCH" ]; then',
            '  if git show-ref --verify --quiet "refs/heads/$WORK_BRANCH"; then git switch "$WORK_BRANCH"; else git switch -c "$WORK_BRANCH"; fi',
            "fi",
            'git config evander.sessionId "$SESSION_ID"',
            'git config evander.branchSuffix "$BRANCH_SUFFIX"',
            'git config evander.prNotesRef "$PR_NOTES_REF"',
            'git config evander.gitAccessMode "$GIT_ACCESS_MODE"',
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
            git_author_name=git_author_identity.name if git_author_identity is not None else None,
            git_author_email=git_author_identity.email if git_author_identity is not None else None,
            git_committer_name=(
                git_committer_identity.name if git_committer_identity is not None else None
            ),
            git_committer_email=(
                git_committer_identity.email if git_committer_identity is not None else None
            ),
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
    return ContainerGitMirrorResolver(
        project_dir=project_dir,
        git_root=issue_setup.git_root,
        git_host=issue_setup.git_host,
        git_user=issue_setup.git_user,
        git_ssh_alias=issue_setup.git_ssh_alias,
    )


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


def _record_session_git_write_access(
    context: Any,
    session_id: str,
    metadata: dict[str, object],
) -> None:
    patch = {"container_git_write_access": metadata}
    update_target = getattr(context, "session_metadata_updater", None)
    update_fn = getattr(update_target, "session_update_metadata", None)
    if callable(update_fn):
        try:
            update_fn(target=session_id, metadata=patch)
            return
        except Exception:
            pass
    runtime = getattr(getattr(context, "admin_service", None), "runtime", None)
    repository = getattr(runtime, "session_repository", None)
    get_session = getattr(repository, "get_session", None)
    upsert_session = getattr(repository, "upsert_session", None)
    if not callable(get_session) or not callable(upsert_session):
        return
    try:
        session = get_session(session_id)
        if session is None:
            return
        current_metadata = getattr(session, "metadata", {})
        if not isinstance(current_metadata, dict):
            current_metadata = {}
        updated_metadata = {**current_metadata, **patch}
        upsert_session(session.model_copy(update={"metadata": updated_metadata}))
    except Exception:
        return


def _remove_session_git_write_access(
    context: Any,
    session_id: str,
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
    git_access = current_metadata.get("container_git_write_access")
    if not isinstance(git_access, dict):
        return
    public_key_body = normalize_optional_string(git_access.get("public_key_body"))
    authorized_keys_path = normalize_optional_string(git_access.get("authorized_keys_path"))
    if public_key_body and authorized_keys_path:
        remove_git_authorized_key_by_public_key_body(
            authorized_keys_path=authorized_keys_path,
            public_key_body=public_key_body,
        )
    updated_metadata = dict(current_metadata)
    updated_metadata.pop("container_git_write_access", None)
    upsert_session(session.model_copy(update={"metadata": updated_metadata}))


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
