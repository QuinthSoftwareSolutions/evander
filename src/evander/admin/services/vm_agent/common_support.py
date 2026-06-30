from __future__ import annotations

from evander.admin.services.vm_agent.common_imports import (
    dataclass,
    AgentCreatePort,
    AgentPromptDispatchPort,
    Any,
    ContainerWorkerBackendPort,
    DirectoryPathPort,
    LocalIssueIdentityResolver,
    Path,
    PrivilegedHelperPort,
    SessionManifest,
    VmDefaultsConfigResolver,
    VmLifecyclePort,
    WorkflowConfigPort,
    agent_pr_notes_ref_for_session,
    git_helpers,
    os,
    process_helpers,
    project_helpers,
    resolve_agent_model_for_manifest,
    resolve_effective_issue_setup_config,
    session_helpers,
    shlex,
    vm_helpers,
)


@dataclass(frozen=True)
class VmAgentAdminDependencies:
    config: Any
    runtime: Any
    project_dir: Path
    local_issue_store: Any
    vm_inventory_admin: Any
    agent_control_store: Any
    directory_ownership: Any
    privileged_helper: PrivilegedHelperPort
    directory_path: DirectoryPathPort
    workflow_config: WorkflowConfigPort
    resolve_agent_model_for_session: Any
    resolve_vm_defaults: Any
    resolve_issue_setup: Any
    resolve_local_issue_record: Any
    resolve_local_issue_key: Any
    resolve_repo_source: Any
    sync_repo_into_bare_remote: Any
    clone_repo_into_vm: Any
    vm_lifecycle: VmLifecyclePort | None = None
    agent_create: AgentCreatePort | None = None
    agent_prompt_dispatch: AgentPromptDispatchPort | None = None
    container_worker_backend_port: ContainerWorkerBackendPort | None = None


@dataclass(frozen=True)
class _VmProviderRegistryDependencies:
    runtime: Any
    directory_path: DirectoryPathPort
    resolve_vm_defaults: Any
    resolve_agent_model_for_session: Any
    privileged_helper: PrivilegedHelperPort
    directory_ownership: Any
    agent_control_store: Any

    def normalize_directory_path(self, directory_path: str | None) -> str:
        return self.directory_path.normalize_directory_path(directory_path)

    def get_privileged_helper(self) -> Any | None:
        return self.privileged_helper.get_privileged_helper()

    def get_use_privileged_helper(self) -> bool:
        return self.privileged_helper.get_use_privileged_helper()

    def direct_owned_entity_ids(self, directory_path: str, *, kind: str = "vm") -> set[str]:
        return self.directory_ownership.direct_owned_entity_ids(directory_path, kind=kind)

    def sync_added_discord_session_cursors(
        self, *, previous_session: Any, updated_session: Any
    ) -> None:
        session_helpers.sync_added_discord_session_cursors(
            self.runtime,
            previous_session=previous_session,
            updated_session=updated_session,
        )


class _EmptyDirectoryOwnership:
    def direct_owned_entity_ids(self, directory_path: str, *, kind: str = "vm") -> set[str]:
        del directory_path, kind
        return set()


class _EnabledAgentControlStore:
    def status_for_vm(self, vm_name: str) -> str:
        del vm_name
        return "enabled"


class _CallbackPrivilegedHelperPort:
    def __init__(
        self,
        get_privileged_helper: Any | None,
        get_use_privileged_helper: Any | None,
    ) -> None:
        self._get_privileged_helper = get_privileged_helper or (lambda: None)
        self._get_use_privileged_helper = get_use_privileged_helper or (lambda: False)

    def get_privileged_helper(self) -> Any | None:
        return self._get_privileged_helper()

    def get_use_privileged_helper(self) -> bool:
        return bool(self._get_use_privileged_helper())

    def set_privileged_helper(self, helper: Any | None) -> None:
        self._get_privileged_helper = lambda: helper


@dataclass
class VmAgentAdminSupport:
    config: Any
    runtime: Any
    project_dir: Path
    local_issue_store: Any
    local_issue_identity_resolver: LocalIssueIdentityResolver
    vm_inventory_admin: Any
    workflow_config_admin: Any
    directory_admin: Any
    agent_control_store: Any | None = None
    directory_ownership: Any | None = None
    privileged_helper: PrivilegedHelperPort | None = None
    get_privileged_helper: Any | None = None
    get_use_privileged_helper: Any | None = None

    def __post_init__(self) -> None:
        if self.privileged_helper is None:
            self.privileged_helper = _CallbackPrivilegedHelperPort(
                self.get_privileged_helper,
                self.get_use_privileged_helper,
            )
        if self.agent_control_store is None:
            self.agent_control_store = _EnabledAgentControlStore()
        if self.directory_ownership is None:
            self.directory_ownership = _EmptyDirectoryOwnership()

    def normalize_directory_path(self, directory_path: str | None) -> str:
        if not directory_path:
            return "root"
        result = self.directory_admin.get(directory_path)
        if not result.ok or result.data is None:
            return "root"
        return result.data.details.directory.path

    def read_worker_global_config(self, worker_id: str) -> Any:
        return self.workflow_config_admin.read_worker_global_config(worker_id)

    def resolve_vm_defaults(self, directory_path: str) -> Any:
        resolver = VmDefaultsConfigResolver(
            self.config,
            directory_store=self.runtime.directories,
            session_store=self.runtime.session_repository,
        )
        return resolver.resolve_for_directory(directory_path)

    def resolve_issue_setup(self, directory_path: str, *, root_dir: Path) -> Any:
        return resolve_effective_issue_setup_config(
            config=self.config,
            runtime=self.runtime,
            directory_path=directory_path,
            root_dir=root_dir,
        )

    def resolve_agent_model_for_session(self, session_or_id: Any) -> str:
        if isinstance(session_or_id, SessionManifest):
            session = session_or_id
            session_id = session.session_id
        else:
            session_id = str(session_or_id).strip()
            if not session_id:
                raise ValueError(
                    "Cannot resolve AGENT_MODEL: missing session id. Provide a valid session id or SessionManifest."
                )
            session = self.runtime.session_repository.get_session(session_id)
            if session is None:
                raise ValueError(
                    f"Cannot resolve AGENT_MODEL for session '{session_id}': session not found. "
                    "Remediation: create the session first or pass a SessionManifest with session.model set."
                )
        issue_setup = self.resolve_issue_setup(session.directory_path, root_dir=self.project_dir)
        return resolve_agent_model_for_manifest(session, issue_setup=issue_setup)

    def resolve_local_issue_record(self, issue_identifier: str) -> Any:
        return self.local_issue_identity_resolver.resolve_record(issue_identifier)

    def resolve_local_issue_key(self, identifier: str) -> str | None:
        return self.local_issue_identity_resolver.resolve_key(identifier)

    def find_local_issue_by_remote_identifier(self, issue_identifier: str) -> Any | None:
        return self.local_issue_identity_resolver.find_by_remote_identifier(issue_identifier)

    def resolve_repo_source(self, repo_source: str) -> tuple[str, str]:
        resolve_script = self.project_dir / "scripts" / "resolve_repo_source.sh"
        path_result = process_helpers.run_subprocess(
            [str(resolve_script), "--path-only", repo_source], capture_output=True, text=True
        )
        if path_result.returncode != 0:
            raise RuntimeError(
                (path_result.stderr or path_result.stdout or "").strip()
                or f"Failed to resolve repo source: {repo_source}"
            )
        name_result = process_helpers.run_subprocess(
            [str(resolve_script), "--name-only", repo_source], capture_output=True, text=True
        )
        if name_result.returncode != 0:
            raise RuntimeError(
                (name_result.stderr or name_result.stdout or "").strip()
                or f"Failed to determine repo name: {repo_source}"
            )
        return (path_result.stdout or "").strip(), (name_result.stdout or "").strip()

    def sync_repo_into_bare_remote(
        self,
        repo_name: str,
        resolved_repo_path: str,
        *,
        issue_setup: Any,
        repo_source: str | None = None,
    ) -> Path:
        create_remote_script = self.project_dir / "scripts" / "create_git_remote.sh"
        completed = process_helpers.run_subprocess(
            [str(create_remote_script), repo_name, "agents"],
            capture_output=True,
            text=True,
            env=os.environ
            | {
                "EVANDER_GIT_ROOT": issue_setup.git_root,
                "EVANDER_GIT_HOST": issue_setup.git_host,
                "EVANDER_GIT_USER": issue_setup.git_user,
                "EVANDER_GIT_SSH_ALIAS": issue_setup.git_ssh_alias,
            },
        )
        if completed.returncode != 0:
            raise RuntimeError(
                (completed.stderr or completed.stdout or "").strip()
                or f"Failed to create git remote for {repo_name}"
            )
        bare_repo_path = Path(issue_setup.git_root) / f"{repo_name}.git"
        fetch_result = process_helpers.run_subprocess(
            [
                "git",
                f"--git-dir={bare_repo_path}",
                "fetch",
                "--prune",
                resolved_repo_path,
                "+refs/heads/*:refs/heads/*",
                "+refs/tags/*:refs/tags/*",
                *git_helpers.PROTECTED_MIRROR_HEAD_REFSPECS,
            ],
            capture_output=True,
            text=True,
        )
        if fetch_result.returncode != 0:
            raise RuntimeError(
                (fetch_result.stderr or fetch_result.stdout or "").strip()
                or f"Failed to mirror repo into {bare_repo_path}"
            )
        trusted_repo_result = process_helpers.run_subprocess(
            [
                "git",
                f"--git-dir={bare_repo_path}",
                "config",
                "evander.trustedRepoPath",
                resolved_repo_path,
            ],
            capture_output=True,
            text=True,
        )
        if trusted_repo_result.returncode != 0:
            raise RuntimeError(
                (trusted_repo_result.stderr or trusted_repo_result.stdout or "").strip()
                or f"Failed to record trusted repo path for {bare_repo_path}"
            )
        mirror_source = (repo_source or resolved_repo_path).strip()
        for key, value in (
            ("evander.mirrorSource", mirror_source),
            (
                "evander.mirrorRemoteName",
                git_helpers.mirror_remote_name_for_repo(resolved_repo_path),
            ),
            ("evander.mirrorSyncRemote", "1"),
            ("evander.mirrorForceLocal", "0"),
        ):
            result = process_helpers.run_subprocess(
                ["git", f"--git-dir={bare_repo_path}", "config", key, value],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    (result.stderr or result.stdout or "").strip()
                    or f"Failed to record {key} for {bare_repo_path}"
                )
        mirror_remote_name = git_helpers.mirror_remote_name_for_repo(resolved_repo_path)
        canonical_origin_url = git_helpers.resolve_canonical_origin_url_for_repo(
            resolved_repo_path, mirror_remote_name
        )
        if canonical_origin_url:
            canonical_config_result = process_helpers.run_subprocess(
                [
                    "git",
                    f"--git-dir={bare_repo_path}",
                    "config",
                    "evander.canonicalOriginUrl",
                    canonical_origin_url,
                ],
                capture_output=True,
                text=True,
            )
            if canonical_config_result.returncode != 0:
                raise RuntimeError(
                    (canonical_config_result.stderr or canonical_config_result.stdout or "").strip()
                    or f"Failed to record canonical origin URL for {bare_repo_path}"
                )
        default_base_branch = git_helpers.default_base_branch_for_repo_remote(
            resolved_repo_path, mirror_remote_name
        )
        if default_base_branch:
            base_branch_config_result = process_helpers.run_subprocess(
                [
                    "git",
                    f"--git-dir={bare_repo_path}",
                    "config",
                    "evander.baseBranch",
                    default_base_branch,
                ],
                capture_output=True,
                text=True,
            )
            if base_branch_config_result.returncode != 0:
                raise RuntimeError(
                    (
                        base_branch_config_result.stderr or base_branch_config_result.stdout or ""
                    ).strip()
                    or f"Failed to record base branch for {bare_repo_path}"
                )
            symbolic_ref_result = process_helpers.run_subprocess(
                [
                    "git",
                    f"--git-dir={bare_repo_path}",
                    "symbolic-ref",
                    "HEAD",
                    f"refs/heads/{default_base_branch}",
                ],
                capture_output=True,
                text=True,
            )
            if symbolic_ref_result.returncode != 0:
                raise RuntimeError(
                    (symbolic_ref_result.stderr or symbolic_ref_result.stdout or "").strip()
                    or f"Failed to set HEAD for {bare_repo_path}"
                )
        return bare_repo_path

    def clone_repo_into_vm(
        self,
        *,
        vm_name: str,
        vm_user: str,
        session_id: str,
        repo_name: str,
        issue_setup: Any,
        repo_source: str | None = None,
        clone_branch: str | None = None,
        issue_number: str | None = None,
        vm_ip: str | None = None,
        git_author_name: str | None = None,
        git_author_email: str | None = None,
        git_committer_name: str | None = None,
        git_committer_email: str | None = None,
    ) -> str:
        bootstrap_git_script = self.project_dir / "scripts" / "bootstrap_git_vm.sh"
        completed = process_helpers.run_subprocess(
            [str(bootstrap_git_script), vm_name, vm_user],
            capture_output=True,
            text=True,
            env=os.environ
            | {
                "EVANDER_GIT_ROOT": issue_setup.git_root,
                "EVANDER_GIT_HOST": issue_setup.git_host,
                "EVANDER_GIT_USER": issue_setup.git_user,
                "EVANDER_GIT_SSH_ALIAS": issue_setup.git_ssh_alias,
            },
        )
        if completed.returncode != 0:
            raise RuntimeError(
                (completed.stderr or completed.stdout or "").strip()
                or f"Failed to bootstrap guest git for {vm_name}"
            )

        vm_ip = vm_ip or vm_helpers.resolve_vm_ip(vm_name)
        ssh_private_key_path = vm_helpers.resolve_private_key_path()
        if not vm_ip or ssh_private_key_path is None:
            raise RuntimeError(f"Unable to resolve SSH access for {vm_name}")

        remote_url = f"ssh://{issue_setup.git_ssh_alias}{issue_setup.git_root}/{repo_name}.git"
        pr_notes_ref = agent_pr_notes_ref_for_session(session_id)
        canonical_remote_url = (
            repo_source.strip()
            if repo_source and git_helpers.is_remote_git_source(repo_source)
            else ""
        )
        guest_repo_root = f"/home/{vm_user}/{repo_name}"
        agent_pr_note_helper_b64 = project_helpers.guest_agent_pr_note_helper_b64(self.project_dir)
        agent_push_helper_b64 = project_helpers.guest_push_agent_helper_b64(self.project_dir)
        sync_base_helper_b64 = project_helpers.guest_sync_base_helper_b64(self.project_dir)
        clone_branch_line = ""
        clone_command = 'git clone "${REMOTE_URL}" "${TARGET_DIR}"'
        if clone_branch:
            clone_command = 'git clone --branch "${CLONE_BRANCH}" "${REMOTE_URL}" "${TARGET_DIR}"'
            clone_branch_line = f'CLONE_BRANCH="{clone_branch}"\n'
        issue_config_line = ""
        if issue_number:
            issue_config_line = (
                f'git -C "${{TARGET_DIR}}" config evander.issueNumber "{issue_number}"\n'
                f'git -C "${{TARGET_DIR}}" config evander.destinationBranch "feature/issue-{issue_number}"\n'
            )
        branch_suffix_line = (
            'git -C "${TARGET_DIR}" config evander.branchSuffix "${VM_NAME}"\n'
        )
        normalized_author_name = str(git_author_name or "").strip()
        normalized_author_email = str(git_author_email or "").strip()
        if bool(normalized_author_name) != bool(normalized_author_email):
            raise ValueError("git_author_name and git_author_email must both be set")
        normalized_committer_name = str(git_committer_name or "").strip()
        normalized_committer_email = str(git_committer_email or "").strip()
        if bool(normalized_committer_name) != bool(normalized_committer_email):
            raise ValueError("git_committer_name and git_committer_email must both be set")
        git_user_name = normalized_author_name or "Truman Bot"
        git_user_email = normalized_author_email or "truman@quinth.se"
        git_identity_config_line = ""
        if normalized_author_name and normalized_author_email:
            git_identity_config_line += (
                'git -C "${TARGET_DIR}" config author.name "${GIT_AUTHOR_NAME}"\n'
                'git -C "${TARGET_DIR}" config author.email "${GIT_AUTHOR_EMAIL}"\n'
                'git -C "${TARGET_DIR}" config evander.gitAuthorName "${GIT_AUTHOR_NAME}"\n'
                'git -C "${TARGET_DIR}" config evander.gitAuthorEmail "${GIT_AUTHOR_EMAIL}"\n'
            )
        if normalized_committer_name and normalized_committer_email:
            git_identity_config_line += (
                'git -C "${TARGET_DIR}" config committer.name "${GIT_COMMITTER_NAME}"\n'
                'git -C "${TARGET_DIR}" config committer.email "${GIT_COMMITTER_EMAIL}"\n'
                'git -C "${TARGET_DIR}" config evander.gitCommitterName "${GIT_COMMITTER_NAME}"\n'
                'git -C "${TARGET_DIR}" config evander.gitCommitterEmail "${GIT_COMMITTER_EMAIL}"\n'
            )
        remote_script = f"""
set -euo pipefail
. ~/.profile
TARGET_DIR="{guest_repo_root}"
REMOTE_URL="{remote_url}"
CANONICAL_REMOTE_URL="{canonical_remote_url}"
SESSION_ID="{session_id}"
VM_NAME="{vm_name}"
PR_NOTES_REF="{pr_notes_ref}"
GIT_USER_NAME={shlex.quote(git_user_name)}
GIT_USER_EMAIL={shlex.quote(git_user_email)}
GIT_AUTHOR_NAME={shlex.quote(normalized_author_name)}
GIT_AUTHOR_EMAIL={shlex.quote(normalized_author_email)}
GIT_COMMITTER_NAME={shlex.quote(normalized_committer_name)}
GIT_COMMITTER_EMAIL={shlex.quote(normalized_committer_email)}
AGENT_PR_NOTE_HELPER_B64="{agent_pr_note_helper_b64}"
AGENT_PUSH_HELPER_B64="{agent_push_helper_b64}"
SYNC_BASE_HELPER_B64="{sync_base_helper_b64}"
WORK_BRANCH="work"
{clone_branch_line}\
if [[ -e "${{TARGET_DIR}}" ]]; then
  if [[ ! -d "${{TARGET_DIR}}/.git" ]]; then
    echo "Guest repo path exists but is not a Git worktree: ${{TARGET_DIR}}" >&2
    exit 1
  fi
  REPO_ALREADY_PREPARED=1
else
  REPO_ALREADY_PREPARED=0
  {clone_command}
fi
if [[ -n "${{CANONICAL_REMOTE_URL}}" ]]; then
  if git -C "${{TARGET_DIR}}" remote get-url origin >/dev/null 2>&1; then
    git -C "${{TARGET_DIR}}" remote set-url origin "${{CANONICAL_REMOTE_URL}}"
  else
    git -C "${{TARGET_DIR}}" remote add origin "${{CANONICAL_REMOTE_URL}}"
  fi
  git -C "${{TARGET_DIR}}" config "url.${{REMOTE_URL}}.insteadOf" "${{CANONICAL_REMOTE_URL}}"
  if git -C "${{TARGET_DIR}}" remote get-url agent >/dev/null 2>&1; then
    git -C "${{TARGET_DIR}}" remote set-url agent "${{REMOTE_URL}}"
  else
    git -C "${{TARGET_DIR}}" remote add agent "${{REMOTE_URL}}"
  fi
else
  if git -C "${{TARGET_DIR}}" remote get-url origin >/dev/null 2>&1; then
    git -C "${{TARGET_DIR}}" remote set-url origin "${{REMOTE_URL}}"
  else
    git -C "${{TARGET_DIR}}" remote add origin "${{REMOTE_URL}}"
  fi
  if git -C "${{TARGET_DIR}}" remote get-url agent >/dev/null 2>&1; then
    git -C "${{TARGET_DIR}}" remote set-url agent "${{REMOTE_URL}}"
  else
    git -C "${{TARGET_DIR}}" remote add agent "${{REMOTE_URL}}"
  fi
fi
git -C "${{TARGET_DIR}}" config user.name "${{GIT_USER_NAME}}"
git -C "${{TARGET_DIR}}" config user.email "${{GIT_USER_EMAIL}}"
BASE_BRANCH="${{CLONE_BRANCH:-}}"
if [[ -z "${{BASE_BRANCH}}" ]]; then
  BASE_BRANCH="$(git -C "${{TARGET_DIR}}" branch --show-current || true)"
fi
if [[ -n "${{BASE_BRANCH}}" ]]; then
  git -C "${{TARGET_DIR}}" config evander.baseBranch "${{BASE_BRANCH}}"
fi
if [[ "${{REPO_ALREADY_PREPARED}}" == "0" ]]; then
  git -C "${{TARGET_DIR}}" config evander.workBranch "${{WORK_BRANCH}}"
  CURRENT_BRANCH="$(git -C "${{TARGET_DIR}}" branch --show-current || true)"
  if [[ "${{CURRENT_BRANCH}}" != "${{WORK_BRANCH}}" ]]; then
    if git -C "${{TARGET_DIR}}" show-ref --verify --quiet "refs/heads/${{WORK_BRANCH}}"; then
      git -C "${{TARGET_DIR}}" switch "${{WORK_BRANCH}}"
    else
      git -C "${{TARGET_DIR}}" switch -c "${{WORK_BRANCH}}"
    fi
  fi
else
  if ! git -C "${{TARGET_DIR}}" config --get evander.workBranch >/dev/null 2>&1; then
    CURRENT_BRANCH="$(git -C "${{TARGET_DIR}}" branch --show-current || true)"
    if [[ -n "${{CURRENT_BRANCH}}" ]]; then
      git -C "${{TARGET_DIR}}" config evander.workBranch "${{CURRENT_BRANCH}}"
    else
      git -C "${{TARGET_DIR}}" config evander.workBranch "${{WORK_BRANCH}}"
    fi
  fi
fi
git -C "${{TARGET_DIR}}" config evander.sessionId "${{SESSION_ID}}"
git -C "${{TARGET_DIR}}" config evander.prNotesRef "${{PR_NOTES_REF}}"
{issue_config_line}{branch_suffix_line}{git_identity_config_line}install -d -m 0755 "${{TARGET_DIR}}/.evander/bin"
python3 - "${{TARGET_DIR}}/.evander/bin/agent-pr-note" "${{AGENT_PR_NOTE_HELPER_B64}}" <<'PY'
import base64
import os
import sys
import logging
from pathlib import Path

target_path = Path(sys.argv[1])
target_path.write_bytes(base64.b64decode(sys.argv[2]))
os.chmod(target_path, 0o755)
PY
python3 - "${{TARGET_DIR}}/.evander/bin/push-agent" "${{AGENT_PUSH_HELPER_B64}}" <<'PY'
import base64
import os
import sys
from pathlib import Path

target_path = Path(sys.argv[1])
target_path.write_bytes(base64.b64decode(sys.argv[2]))
os.chmod(target_path, 0o755)
PY
python3 - "${{TARGET_DIR}}/.evander/bin/sync-base" "${{SYNC_BASE_HELPER_B64}}" <<'PY'
import base64
import os
import sys
from pathlib import Path

target_path = Path(sys.argv[1])
target_path.write_bytes(base64.b64decode(sys.argv[2]))
os.chmod(target_path, 0o755)
PY
git -C "${{TARGET_DIR}}" config alias.agent-pr-note '!f(){{ repo_root=$(git rev-parse --show-toplevel); "${{repo_root}}/.evander/bin/agent-pr-note" "$@"; }}; f'
git -C "${{TARGET_DIR}}" config alias.push-agent '!f(){{ repo_root=$(git rev-parse --show-toplevel); "${{repo_root}}/.evander/bin/push-agent" "$@"; }}; f'
git -C "${{TARGET_DIR}}" config alias.sync-base '!f(){{ repo_root=$(git rev-parse --show-toplevel); "${{repo_root}}/.evander/bin/sync-base" "$@"; }}; f'
"""
        ssh_result = process_helpers.run_subprocess(
            [
                "ssh",
                *vm_helpers.ssh_options(vm_name, vm_ip, ssh_private_key_path),
                f"{vm_user}@{vm_ip}",
                "bash",
                "-s",
            ],
            capture_output=True,
            text=True,
            timeout=60.0,
            input=remote_script,
        )
        if ssh_result.returncode != 0:
            raise RuntimeError(
                (ssh_result.stderr or ssh_result.stdout or "").strip()
                or f"Failed to clone repo into {vm_name}"
            )
        return guest_repo_root


__all__ = [
    "VmAgentAdminDependencies",
    "VmAgentAdminSupport",
    "_CallbackPrivilegedHelperPort",
    "_EmptyDirectoryOwnership",
    "_EnabledAgentControlStore",
    "_VmProviderRegistryDependencies",
]
