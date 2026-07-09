from __future__ import annotations

from evander.admin.services.vm_agent.common import (
    EffectiveIssueSetupConfig,
    Path,
    _PROTECTED_MIRROR_HEAD_REFSPECS,
    _default_base_branch_for_repo_remote,
    _guest_agent_pr_note_helper_b64,
    _guest_push_agent_helper_b64,
    _guest_sync_base_helper_b64,
    _is_remote_git_source,
    _mirror_remote_name_for_repo,
    _resolve_canonical_origin_url_for_repo,
    _resolve_private_key_path,
    _resolve_vm_ip,
    _run_subprocess,
    _ssh_options,
    agent_pr_notes_ref_for_session,
    os,
)


class VmAgentRepoMixin:
    def _record_mirror_secrets_env_file(self, bare_repo_path: Path) -> None:
        secrets_env_file = os.environ.get("EVANDER_SECRETS_ENV_FILE", "").strip()
        if not secrets_env_file:
            return
        if "\n" in secrets_env_file or "\r" in secrets_env_file:
            raise RuntimeError("EVANDER_SECRETS_ENV_FILE must be a single-line path")
        result = _run_subprocess(
            [
                "git",
                f"--git-dir={bare_repo_path}",
                "config",
                "evander.secretsEnvFile",
                secrets_env_file,
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                (result.stderr or result.stdout or "").strip()
                or f"Failed to record secrets env file for {bare_repo_path}"
            )

    def _resolve_repo_source(self, repo_source: str) -> tuple[str, str]:
        resolve_script = self.project_dir / "scripts" / "resolve_repo_source.sh"
        path_result = _run_subprocess(
            [str(resolve_script), "--path-only", repo_source], capture_output=True, text=True
        )
        if path_result.returncode != 0:
            raise RuntimeError(
                (path_result.stderr or path_result.stdout or "").strip()
                or f"Failed to resolve repo source: {repo_source}"
            )
        name_result = _run_subprocess(
            [str(resolve_script), "--name-only", repo_source], capture_output=True, text=True
        )
        if name_result.returncode != 0:
            raise RuntimeError(
                (name_result.stderr or name_result.stdout or "").strip()
                or f"Failed to determine repo name: {repo_source}"
            )
        return (path_result.stdout or "").strip(), (name_result.stdout or "").strip()

    def _sync_repo_into_bare_remote(
        self,
        repo_name: str,
        resolved_repo_path: str,
        *,
        issue_setup: EffectiveIssueSetupConfig,
        repo_source: str | None = None,
    ) -> Path:
        create_remote_script = self.project_dir / "scripts" / "create_git_remote.sh"
        completed = _run_subprocess(
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
        fetch_result = _run_subprocess(
            [
                "git",
                f"--git-dir={bare_repo_path}",
                "fetch",
                "--prune",
                resolved_repo_path,
                "+refs/heads/*:refs/heads/*",
                "+refs/tags/*:refs/tags/*",
                *_PROTECTED_MIRROR_HEAD_REFSPECS,
            ],
            capture_output=True,
            text=True,
        )
        if fetch_result.returncode != 0:
            raise RuntimeError(
                (fetch_result.stderr or fetch_result.stdout or "").strip()
                or f"Failed to mirror repo into {bare_repo_path}"
            )
        trusted_repo_result = _run_subprocess(
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
        mirror_source_result = _run_subprocess(
            [
                "git",
                f"--git-dir={bare_repo_path}",
                "config",
                "evander.mirrorSource",
                mirror_source,
            ],
            capture_output=True,
            text=True,
        )
        if mirror_source_result.returncode != 0:
            raise RuntimeError(
                (mirror_source_result.stderr or mirror_source_result.stdout or "").strip()
                or f"Failed to record mirror source for {bare_repo_path}"
            )
        mirror_remote_name = _mirror_remote_name_for_repo(resolved_repo_path)
        mirror_remote_result = _run_subprocess(
            [
                "git",
                f"--git-dir={bare_repo_path}",
                "config",
                "evander.mirrorRemoteName",
                mirror_remote_name,
            ],
            capture_output=True,
            text=True,
        )
        if mirror_remote_result.returncode != 0:
            raise RuntimeError(
                (mirror_remote_result.stderr or mirror_remote_result.stdout or "").strip()
                or f"Failed to record mirror remote name for {bare_repo_path}"
            )
        mirror_sync_result = _run_subprocess(
            ["git", f"--git-dir={bare_repo_path}", "config", "evander.mirrorSyncRemote", "1"],
            capture_output=True,
            text=True,
        )
        if mirror_sync_result.returncode != 0:
            raise RuntimeError(
                (mirror_sync_result.stderr or mirror_sync_result.stdout or "").strip()
                or f"Failed to record mirror sync policy for {bare_repo_path}"
            )
        mirror_force_local_result = _run_subprocess(
            ["git", f"--git-dir={bare_repo_path}", "config", "evander.mirrorForceLocal", "0"],
            capture_output=True,
            text=True,
        )
        if mirror_force_local_result.returncode != 0:
            raise RuntimeError(
                (mirror_force_local_result.stderr or mirror_force_local_result.stdout or "").strip()
                or f"Failed to record mirror force-local policy for {bare_repo_path}"
            )
        self._record_mirror_secrets_env_file(bare_repo_path)
        canonical_origin_url = _resolve_canonical_origin_url_for_repo(
            resolved_repo_path, mirror_remote_name
        )
        if canonical_origin_url:
            canonical_config_result = _run_subprocess(
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
        default_base_branch = _default_base_branch_for_repo_remote(
            resolved_repo_path, mirror_remote_name
        )
        if default_base_branch:
            base_branch_config_result = _run_subprocess(
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
            symbolic_ref_result = _run_subprocess(
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

    def _clone_repo_into_vm(
        self,
        *,
        vm_name: str,
        vm_user: str,
        session_id: str,
        repo_name: str,
        issue_setup: EffectiveIssueSetupConfig,
        repo_source: str | None = None,
        clone_branch: str | None = None,
        issue_number: str | None = None,
        vm_ip: str | None = None,
    ) -> str:
        bootstrap_git_script = self.project_dir / "scripts" / "bootstrap_git_vm.sh"
        completed = _run_subprocess(
            [str(bootstrap_git_script), vm_name, vm_user],
            capture_output=True,
            text=True,
            env=os.environ
            | {
                "EVANDER_GIT_ROOT": issue_setup.git_root,
                "EVANDER_GIT_HOST": issue_setup.git_host,
                "EVANDER_GIT_USER": issue_setup.git_user,
                "EVANDER_GIT_SSH_ALIAS": issue_setup.git_ssh_alias,
                "EVANDER_GIT_REPO": repo_name,
                "EVANDER_GIT_SESSION_ID": session_id,
            },
        )
        if completed.returncode != 0:
            raise RuntimeError(
                (completed.stderr or completed.stdout or "").strip()
                or f"Failed to bootstrap guest git for {vm_name}"
            )

        vm_ip = vm_ip or _resolve_vm_ip(vm_name)
        ssh_private_key_path = _resolve_private_key_path()
        if not vm_ip or ssh_private_key_path is None:
            raise RuntimeError(f"Unable to resolve SSH access for {vm_name}")

        remote_url = f"ssh://{issue_setup.git_ssh_alias}{issue_setup.git_root}/{repo_name}.git"
        pr_notes_ref = agent_pr_notes_ref_for_session(session_id)
        canonical_remote_url = (
            repo_source.strip() if repo_source and _is_remote_git_source(repo_source) else ""
        )
        guest_repo_root = f"/home/{vm_user}/{repo_name}"
        agent_pr_note_helper_b64 = _guest_agent_pr_note_helper_b64(self.project_dir)
        agent_push_helper_b64 = _guest_push_agent_helper_b64(self.project_dir)
        sync_base_helper_b64 = _guest_sync_base_helper_b64(self.project_dir)
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
        remote_script = f"""
set -euo pipefail
. ~/.profile
TARGET_DIR="{guest_repo_root}"
REMOTE_URL="{remote_url}"
CANONICAL_REMOTE_URL="{canonical_remote_url}"
SESSION_ID="{session_id}"
VM_NAME="{vm_name}"
PR_NOTES_REF="{pr_notes_ref}"
AGENT_PR_NOTE_HELPER_B64="{agent_pr_note_helper_b64}"
AGENT_PUSH_HELPER_B64="{agent_push_helper_b64}"
SYNC_BASE_HELPER_B64="{sync_base_helper_b64}"
WORK_BRANCH="work"
{clone_branch_line}\
if [[ -e "${{TARGET_DIR}}" ]]; then
  echo "Guest repo path already exists: ${{TARGET_DIR}}" >&2
  exit 1
fi
{clone_command}
if [[ -n "${{CANONICAL_REMOTE_URL}}" ]]; then
  git -C "${{TARGET_DIR}}" remote set-url origin "${{CANONICAL_REMOTE_URL}}"
  git -C "${{TARGET_DIR}}" config "url.${{REMOTE_URL}}.insteadOf" "${{CANONICAL_REMOTE_URL}}"
  git -C "${{TARGET_DIR}}" remote add agent "${{REMOTE_URL}}"
else
  git -C "${{TARGET_DIR}}" remote add agent "${{REMOTE_URL}}"
fi
git -C "${{TARGET_DIR}}" config user.name "Truman Bot"
git -C "${{TARGET_DIR}}" config user.email "truman@quinth.se"
BASE_BRANCH="${{CLONE_BRANCH:-}}"
if [[ -z "${{BASE_BRANCH}}" ]]; then
  BASE_BRANCH="$(git -C "${{TARGET_DIR}}" branch --show-current || true)"
fi
if [[ -n "${{BASE_BRANCH}}" ]]; then
  git -C "${{TARGET_DIR}}" config evander.baseBranch "${{BASE_BRANCH}}"
fi
git -C "${{TARGET_DIR}}" config evander.workBranch "${{WORK_BRANCH}}"
CURRENT_BRANCH="$(git -C "${{TARGET_DIR}}" branch --show-current || true)"
if [[ "${{CURRENT_BRANCH}}" != "${{WORK_BRANCH}}" ]]; then
  if git -C "${{TARGET_DIR}}" show-ref --verify --quiet "refs/heads/${{WORK_BRANCH}}"; then
    git -C "${{TARGET_DIR}}" switch "${{WORK_BRANCH}}"
  else
    git -C "${{TARGET_DIR}}" switch -c "${{WORK_BRANCH}}"
  fi
fi
git -C "${{TARGET_DIR}}" config evander.sessionId "${{SESSION_ID}}"
git -C "${{TARGET_DIR}}" config evander.prNotesRef "${{PR_NOTES_REF}}"
{issue_config_line}{branch_suffix_line}install -d -m 0755 "${{TARGET_DIR}}/.evander/bin"
python3 - "${{TARGET_DIR}}/.evander/bin/agent-pr-note" "${{AGENT_PR_NOTE_HELPER_B64}}" <<'PY'
import base64
import os
import sys
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
        ssh_result = _run_subprocess(
            [
                "ssh",
                *_ssh_options(vm_name, vm_ip, ssh_private_key_path),
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

    resolve_repo_source = _resolve_repo_source
    sync_repo_into_bare_remote = _sync_repo_into_bare_remote
    clone_repo_into_vm_guest = _clone_repo_into_vm
