from __future__ import annotations

import base64
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any

from .. import admin_service as _admin_service
from ..models import VmActionResult, VmSummary
from ..session_manifest_repository import as_session_manifest_repository
from ..vm_provider import VmProviderRegistry


@dataclass(frozen=True)
class VmAgentAdminDependencies:
    config: Any
    runtime: Any
    project_dir: Path
    local_issue_store: Any
    worker_admin: Any
    get_privileged_helper: Any
    get_use_privileged_helper: Any
    normalize_directory_path: Any
    read_worker_global_config: Any
    resolve_agent_model_for_session: Any
    resolve_vm_defaults: Any
    resolve_issue_setup: Any
    resolve_local_issue_record: Any
    resolve_local_issue_key: Any
    resolve_repo_source: Any
    sync_repo_into_bare_remote: Any
    clone_repo_into_vm: Any


@dataclass
class VmAgentAdminSupport:
    config: Any
    runtime: Any
    project_dir: Path
    local_issue_store: Any
    worker_admin: Any
    workflow_config_admin: Any
    directory_admin: Any
    get_privileged_helper: Any
    get_use_privileged_helper: Any

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
        resolver = _admin_service.VmDefaultsConfigResolver(
            self.config,
            directory_store=self.runtime.directories,
            session_store=self.runtime.session_repository,
        )
        return resolver.resolve_for_directory(directory_path)

    def resolve_issue_setup(self, directory_path: str, *, root_dir: Path) -> Any:
        return _admin_service.resolve_effective_issue_setup_config(
            config=self.config,
            runtime=self.runtime,
            directory_path=directory_path,
            root_dir=root_dir,
        )

    def resolve_agent_model_for_session(self, session_or_id: Any) -> str:
        if isinstance(session_or_id, _admin_service.SessionManifest):
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
        return _admin_service.resolve_agent_model_for_manifest(session, issue_setup=issue_setup)

    def resolve_local_issue_record(self, issue_identifier: str) -> Any:
        candidate = Path(issue_identifier)
        if candidate.is_file():
            candidate_path = candidate.resolve()
            for issue in self.local_issue_store.read_all().values():
                if issue.yaml_path and Path(issue.yaml_path).resolve() == candidate_path:
                    return issue
            issue_number = _admin_service._read_issue_number_from_yaml(candidate)
            raise ValueError(f"Unknown local issue: {issue_number}")

        issue_key = self.resolve_local_issue_key(issue_identifier)
        if issue_key is not None:
            issue = self.local_issue_store.get(issue_key)
            if issue is not None:
                return issue

        by_remote_identifier = self.find_local_issue_by_remote_identifier(issue_identifier)
        if by_remote_identifier is not None:
            return by_remote_identifier

        raise ValueError(f"Unknown local issue identifier: {issue_identifier}")

    def resolve_local_issue_key(self, identifier: str) -> str | None:
        normalized = str(identifier or "").strip()
        if not normalized:
            return None
        issues = self.local_issue_store.read_all()
        if normalized in issues:
            return normalized
        matches = [issue_id for issue_id, issue in issues.items() if issue.issue_number == normalized]
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0]
        sorted_matches = sorted(matches)
        raise ValueError(
            "Ambiguous local issue identifier: "
            f"{normalized}. Matches: {', '.join(sorted_matches)}. "
            "Use a scoped issue identifier."
        )
        return None

    def find_local_issue_by_remote_identifier(self, issue_identifier: str) -> Any | None:
        normalized = issue_identifier.strip()
        if not normalized:
            return None
        provider = ""
        remote_issue_id = ""
        prefixed_match = _admin_service.re.match(
            r"^(?:(?:proxy:)?(azure_devops|ado)[:/])([0-9]+)$",
            normalized,
            flags=_admin_service.re.IGNORECASE,
        )
        if prefixed_match:
            provider = "azure_devops"
            remote_issue_id = prefixed_match.group(2)
        if not remote_issue_id:
            return None
        matches: list[Any] = []
        for issue in self.local_issue_store.read_all().values():
            remote = issue.remote_reference
            if remote is not None:
                issue_provider = str(remote.provider or "").strip()
                issue_remote_id = str(remote.work_item_id or "").strip()
            else:
                legacy_remote = issue.metadata.get("remote") if isinstance(issue.metadata.get("remote"), dict) else {}
                issue_provider = str(legacy_remote.get("provider") or "").strip()
                issue_remote_id = str(legacy_remote.get("issue_id") or "").strip()
            if issue_provider != provider:
                continue
            if issue_remote_id == remote_issue_id:
                matches.append(issue)
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0]
        sorted_matches = sorted(issue.issue_id for issue in matches)
        raise ValueError(
            "Ambiguous proxy issue identifier: "
            f"{normalized}. Matches: {', '.join(sorted_matches)}. "
            "Use a canonical proxy issue identifier."
        )

    def resolve_repo_source(self, repo_source: str) -> tuple[str, str]:
        resolve_script = self.project_dir / "scripts" / "resolve_repo_source.sh"
        path_result = _admin_service._run_subprocess([str(resolve_script), "--path-only", repo_source], capture_output=True, text=True)
        if path_result.returncode != 0:
            raise RuntimeError((path_result.stderr or path_result.stdout or "").strip() or f"Failed to resolve repo source: {repo_source}")
        name_result = _admin_service._run_subprocess([str(resolve_script), "--name-only", repo_source], capture_output=True, text=True)
        if name_result.returncode != 0:
            raise RuntimeError((name_result.stderr or name_result.stdout or "").strip() or f"Failed to determine repo name: {repo_source}")
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
        completed = _admin_service._run_subprocess(
            [str(create_remote_script), repo_name, "agents"],
            capture_output=True,
            text=True,
            env=os.environ | {
                "AGENT_SANDBOX_GIT_ROOT": issue_setup.git_root,
                "AGENT_SANDBOX_GIT_HOST": issue_setup.git_host,
                "AGENT_SANDBOX_GIT_USER": issue_setup.git_user,
                "AGENT_SANDBOX_GIT_SSH_ALIAS": issue_setup.git_ssh_alias,
            },
        )
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or completed.stdout or "").strip() or f"Failed to create git remote for {repo_name}")
        bare_repo_path = Path(issue_setup.git_root) / f"{repo_name}.git"
        fetch_result = _admin_service._run_subprocess(
            [
                "git",
                f"--git-dir={bare_repo_path}",
                "fetch",
                "--prune",
                resolved_repo_path,
                "+refs/heads/*:refs/heads/*",
                "+refs/tags/*:refs/tags/*",
            ],
            capture_output=True,
            text=True,
        )
        if fetch_result.returncode != 0:
            raise RuntimeError((fetch_result.stderr or fetch_result.stdout or "").strip() or f"Failed to mirror repo into {bare_repo_path}")
        trusted_repo_result = _admin_service._run_subprocess(
            ["git", f"--git-dir={bare_repo_path}", "config", "agent-sandbox.trustedRepoPath", resolved_repo_path],
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
            ("agent-sandbox.mirrorSource", mirror_source),
            ("agent-sandbox.mirrorRemoteName", _admin_service._mirror_remote_name_for_repo(resolved_repo_path)),
            ("agent-sandbox.mirrorSyncRemote", "1"),
            ("agent-sandbox.mirrorForceLocal", "0"),
        ):
            result = _admin_service._run_subprocess(
                ["git", f"--git-dir={bare_repo_path}", "config", key, value],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise RuntimeError((result.stderr or result.stdout or "").strip() or f"Failed to record {key} for {bare_repo_path}")
        mirror_remote_name = _admin_service._mirror_remote_name_for_repo(resolved_repo_path)
        canonical_origin_url = _admin_service._resolve_canonical_origin_url_for_repo(resolved_repo_path, mirror_remote_name)
        if canonical_origin_url:
            canonical_config_result = _admin_service._run_subprocess(
                ["git", f"--git-dir={bare_repo_path}", "config", "agent-sandbox.canonicalOriginUrl", canonical_origin_url],
                capture_output=True,
                text=True,
            )
            if canonical_config_result.returncode != 0:
                raise RuntimeError(
                    (canonical_config_result.stderr or canonical_config_result.stdout or "").strip()
                    or f"Failed to record canonical origin URL for {bare_repo_path}"
                )
        default_base_branch = _admin_service._default_base_branch_for_repo_remote(resolved_repo_path, mirror_remote_name)
        if default_base_branch:
            base_branch_config_result = _admin_service._run_subprocess(
                ["git", f"--git-dir={bare_repo_path}", "config", "agent-sandbox.baseBranch", default_base_branch],
                capture_output=True,
                text=True,
            )
            if base_branch_config_result.returncode != 0:
                raise RuntimeError(
                    (base_branch_config_result.stderr or base_branch_config_result.stdout or "").strip()
                    or f"Failed to record base branch for {bare_repo_path}"
                )
            symbolic_ref_result = _admin_service._run_subprocess(
                ["git", f"--git-dir={bare_repo_path}", "symbolic-ref", "HEAD", f"refs/heads/{default_base_branch}"],
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
    ) -> str:
        bootstrap_git_script = self.project_dir / "scripts" / "bootstrap_git_vm.sh"
        completed = _admin_service._run_subprocess(
            [str(bootstrap_git_script), vm_name, vm_user],
            capture_output=True,
            text=True,
            env=os.environ | {
                "AGENT_SANDBOX_GIT_ROOT": issue_setup.git_root,
                "AGENT_SANDBOX_GIT_HOST": issue_setup.git_host,
                "AGENT_SANDBOX_GIT_USER": issue_setup.git_user,
                "AGENT_SANDBOX_GIT_SSH_ALIAS": issue_setup.git_ssh_alias,
            },
        )
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or completed.stdout or "").strip() or f"Failed to bootstrap guest git for {vm_name}")

        vm_ip = vm_ip or _admin_service._resolve_vm_ip(vm_name)
        ssh_private_key_path = _admin_service._resolve_private_key_path()
        if not vm_ip or ssh_private_key_path is None:
            raise RuntimeError(f"Unable to resolve SSH access for {vm_name}")

        remote_url = f"ssh://{issue_setup.git_ssh_alias}{issue_setup.git_root}/{repo_name}.git"
        canonical_remote_url = repo_source.strip() if repo_source and _admin_service._is_remote_git_source(repo_source) else ""
        guest_repo_root = f"/home/{vm_user}/{repo_name}"
        agent_pr_note_helper_b64 = _admin_service._guest_agent_pr_note_helper_b64(self.project_dir)
        clone_branch_line = ""
        clone_command = 'git clone "${REMOTE_URL}" "${TARGET_DIR}"'
        if clone_branch:
            clone_command = 'git clone --branch "${CLONE_BRANCH}" "${REMOTE_URL}" "${TARGET_DIR}"'
            clone_branch_line = f'CLONE_BRANCH="{clone_branch}"\n'
        issue_config_line = ""
        if issue_number:
            issue_config_line = f'git -C "${{TARGET_DIR}}" config agent-sandbox.issueNumber "{issue_number}"\n'
        branch_suffix_line = 'git -C "${TARGET_DIR}" config agent-sandbox.branchSuffix "${VM_NAME}"\n'
        base_branch_config_line = ""
        if clone_branch:
            base_branch_config_line = 'git -C "${TARGET_DIR}" config agent-sandbox.baseBranch "${CLONE_BRANCH}"\n'
        remote_script = f"""
set -euo pipefail
. ~/.profile
TARGET_DIR="{guest_repo_root}"
REMOTE_URL="{remote_url}"
CANONICAL_REMOTE_URL="{canonical_remote_url}"
SESSION_ID="{session_id}"
VM_NAME="{vm_name}"
AGENT_PR_NOTE_HELPER_B64="{agent_pr_note_helper_b64}"
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
git -C "${{TARGET_DIR}}" config agent-sandbox.sessionId "${{SESSION_ID}}"
{issue_config_line}{branch_suffix_line}{base_branch_config_line}install -d -m 0755 "${{TARGET_DIR}}/.agent-sandbox/bin"
python3 - "${{TARGET_DIR}}/.agent-sandbox/bin/agent-pr-note" "${{AGENT_PR_NOTE_HELPER_B64}}" <<'PY'
import base64
import os
import sys
from pathlib import Path

target_path = Path(sys.argv[1])
target_path.write_bytes(base64.b64decode(sys.argv[2]))
os.chmod(target_path, 0o755)
PY
git -C "${{TARGET_DIR}}" config alias.agent-pr-note '!f(){{ repo_root=$(git rev-parse --show-toplevel); "${{repo_root}}/.agent-sandbox/bin/agent-pr-note" "$@"; }}; f'
git -C "${{TARGET_DIR}}" config alias.push-agent '!f(){{ session_id=${{SESSION_ID:-$(git config --get agent-sandbox.sessionId || true)}}; if [ -z "${{session_id}}" ]; then echo "SESSION_ID is not set; refusing ambiguous agent push." >&2; return 1; fi; expected_issue_number=$(git config --get agent-sandbox.issueNumber || true); if [ -n "${{expected_issue_number}}" ]; then case "${{session_id}}" in issue-${{expected_issue_number}}|issue-${{expected_issue_number}}-*) ;; *) echo "SESSION_ID (${{session_id}}) does not match expected issue ${{expected_issue_number}}; refusing agent push." >&2; return 1 ;; esac; fi; suffix_source=${{AGENT_BRANCH_SUFFIX:-$(git config --get agent-sandbox.branchSuffix || true)}}; if [ -z "${{suffix_source}}" ]; then suffix_source=${{HOSTNAME:-}}; fi; suffix=$(printf "%s" "${{suffix_source}}" | tr "[:upper:]" "[:lower:]" | tr -cs "a-z0-9._-" "-" | sed "s/^-*//;s/-*$//"); if [ -z "${{suffix}}" ]; then echo "AGENT_BRANCH_SUFFIX/HOSTNAME is empty; refusing ambiguous agent push." >&2; return 1; fi; repo_root=$(git rev-parse --show-toplevel); "${{repo_root}}/.agent-sandbox/bin/agent-pr-note" ensure-head; branch=$(git rev-parse --abbrev-ref HEAD); git push agent HEAD:refs/heads/agents/${{session_id}}/${{branch}}-${{suffix}} refs/notes/agent-pr; }}; f'
"""
        ssh_result = _admin_service._run_subprocess(
            ["ssh", *_admin_service._ssh_options(vm_name, vm_ip, ssh_private_key_path), f"{vm_user}@{vm_ip}", "bash", "-s"],
            capture_output=True,
            text=True,
            timeout=60.0,
            input=remote_script,
        )
        if ssh_result.returncode != 0:
            raise RuntimeError((ssh_result.stderr or ssh_result.stdout or "").strip() or f"Failed to clone repo into {vm_name}")
        return guest_repo_root


class VmAgentAdminService:
    def __init__(self, dependencies: VmAgentAdminDependencies) -> None:
        self._deps = dependencies

    def _session_repository(self):
        runtime = self._deps.runtime
        return as_session_manifest_repository(getattr(runtime, "session_repository", runtime.sessions))

    def _resolve_vm_name_from_target(self, target: str) -> str:
        repository = self._session_repository()
        try:
            resolved = repository.resolve_vm_name_from_target(target)
        except Exception:
            resolved = ""
        if resolved and resolved != str(target).strip():
            return resolved
        return _admin_service._resolve_vm_name_from_target(target, self._deps.runtime.sessions.read_all())

    def _resolve_session_target(self, target: str):
        repository = self._session_repository()
        try:
            session = repository.resolve_session_target(target)
        except Exception:
            session = None
        if session is not None:
            return str(getattr(session, "session_id", "") or "").strip(), session
        return _admin_service._resolve_session_target(target, self._deps.runtime.sessions.read_all())

    def _persist_session(self, session_id: str, manifest: Any) -> None:
        repository = self._session_repository()
        manifest_session_id = str(getattr(manifest, "session_id", "") or "").strip()
        if manifest_session_id:
            repository.upsert_session(manifest)
            return
        put = getattr(repository, "put", None)
        if callable(put):
            put(session_id, manifest)
            return
        repository.upsert_session(manifest)

    def _provider_registry(self) -> VmProviderRegistry:
        dependency_view = SimpleNamespace(
            runtime=self._deps.runtime,
            normalize_directory_path=self._deps.normalize_directory_path,
            resolve_vm_defaults=self._deps.resolve_vm_defaults,
            resolve_agent_model_for_session=self._deps.resolve_agent_model_for_session,
            get_privileged_helper=self._deps.get_privileged_helper,
            get_use_privileged_helper=self._deps.get_use_privileged_helper,
            direct_owned_entity_ids=getattr(self._deps.worker_admin, "_deps", SimpleNamespace()).direct_owned_entity_ids
            if hasattr(getattr(self._deps.worker_admin, "_deps", None), "direct_owned_entity_ids")
            else (lambda directory_path, kind="vm": set()),
            agent_control_store=getattr(getattr(self._deps.worker_admin, "_deps", None), "agent_control_store", SimpleNamespace(status_for_vm=lambda vm_name: "enabled")),
        )
        return VmProviderRegistry(dependency_view)

    def _find_session_for_vm(self, vm_name: str) -> tuple[str | None, Any | None]:
        matches: list[tuple[str, Any]] = []
        for session_id, session in self._deps.runtime.sessions.read_all().items():
            if str(getattr(session, "vm_id", "") or "").strip() == vm_name:
                matches.append((session_id, session))
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            session_ids = ", ".join(sorted(session_id for session_id, _ in matches))
            raise ValueError(f"VM {vm_name!r} is associated with multiple sessions: {session_ids}")
        return None, None

    def list_vms(
        self,
        *,
        include_inactive: bool = True,
        omit_state: bool = False,
        vm_user: str = "agent",
        directory_path: str | None = None,
        direct_only: bool = False,
    ) -> list[VmSummary]:
        summaries = self._provider_registry().list_vms(
            include_inactive=include_inactive,
            omit_state=omit_state,
            vm_user=vm_user,
            directory_path=directory_path,
            direct_only=direct_only,
        )
        if summaries:
            return summaries
        return self._deps.worker_admin.list_vms(
            include_inactive=include_inactive,
            omit_state=omit_state,
            vm_user=vm_user,
            directory_path=directory_path,
            direct_only=direct_only,
        )

    def get_agent_log_snapshot(
        self,
        target: str,
        *,
        lines: int = 50,
        repo_name: str | None = None,
        vm_user: str = "agent",
    ) -> str:
        vm_name = self._resolve_vm_name_from_target(target)
        if not vm_name:
            return "Agent log unavailable: no VM target selected."
        vm_state = _admin_service._dominfo_field(vm_name, "State")
        if vm_state != "running":
            return f"Agent log unavailable: {vm_name} is not running."
        vm_ip = _admin_service._resolve_vm_ip(vm_name)
        if not vm_ip:
            return f"Agent log unavailable: could not determine IP for {vm_name}."
        ssh_private_key_path = _admin_service._resolve_private_key_path()
        if ssh_private_key_path is None:
            return "Agent log unavailable: missing SSH private key for guest access."

        remote_script = r"""
set -euo pipefail

LOG_DIR="$HOME/.agent-sandbox"
RESOLVED_REPO_NAME="${REPO_NAME:-}"
TAIL_LINES="${TAIL_LINES:-50}"

if [[ -z "${RESOLVED_REPO_NAME}" ]]; then
  shopt -s nullglob
  candidates=()
  for path in "${LOG_DIR}"/agent-*.pid "${LOG_DIR}"/agent-*.log; do
    name="$(basename "${path}")"
    name="${name#agent-}"
    name="${name%.pid}"
    name="${name%.log}"
    if [[ -n "${name}" ]]; then
      candidates+=("${name}")
    fi
  done
  if [[ -f "${LOG_DIR}/codex-heartbeat-run.log" || -f "${LOG_DIR}/codex-heartbeat.pid" ]]; then
    candidates+=("codex-heartbeat")
  fi
  shopt -u nullglob

  if [[ ${#candidates[@]} -eq 0 ]]; then
    echo "No guest agent logs or pid files found under ${LOG_DIR}"
    exit 0
  fi

  mapfile -t unique_candidates < <(printf '%s\n' "${candidates[@]}" | sort -u)
  if [[ ${#unique_candidates[@]} -ne 1 ]]; then
    echo "Multiple guest agent logs found under ${LOG_DIR}; pass an explicit repo name."
    printf 'Candidates:\n'
    printf '  %s\n' "${unique_candidates[@]}"
    exit 0
  fi

  RESOLVED_REPO_NAME="${unique_candidates[0]}"
fi

if [[ "${RESOLVED_REPO_NAME}" == "codex-heartbeat" ]]; then
  LOG_PATH="${LOG_DIR}/codex-heartbeat-run.log"
else
  LOG_PATH="${LOG_DIR}/agent-${RESOLVED_REPO_NAME}.log"
fi
if [[ ! -f "${LOG_PATH}" ]]; then
  echo "No guest agent log found at ${LOG_PATH}"
  exit 0
fi

echo "Repo: ${RESOLVED_REPO_NAME}"
echo "Log: ${LOG_PATH}"
echo
tail -n "${TAIL_LINES}" "${LOG_PATH}"
"""
        result = _admin_service._run_subprocess(
            [
                "ssh",
                *_admin_service._ssh_options(vm_name, vm_ip, ssh_private_key_path),
                f"{vm_user}@{vm_ip}",
                "env",
                f"REPO_NAME={repo_name or ''}",
                f"TAIL_LINES={lines}",
                "bash",
                "-s",
            ],
            capture_output=True,
            text=True,
            timeout=20.0,
            input=remote_script,
        )
        output = (result.stdout or "").strip()
        error = (result.stderr or "").strip()
        if result.returncode != 0 and error:
            return f"Agent log unavailable: {error}"
        return output or error or "Agent log is empty."

    def start_vm(self, target: str) -> VmActionResult:
        sessions = self._deps.runtime.sessions.read_all()
        provider, vm_name, session = self._provider_registry().resolve_target(
            target=target,
            sessions=sessions,
            directory_path=str(getattr(sessions.get(target), "directory_path", "") or "root"),
        )
        directory_path = str(getattr(session, "directory_path", "") or "root")
        return provider.start_vm(target=target, vm_name=vm_name, session=session, directory_path=directory_path)

    def shutdown_vm(self, target: str, *, timeout_seconds: int = 60) -> VmActionResult:
        sessions = self._deps.runtime.sessions.read_all()
        provider, vm_name, session = self._provider_registry().resolve_target(
            target=target,
            sessions=sessions,
            directory_path=str(getattr(sessions.get(target), "directory_path", "") or "root"),
        )
        directory_path = str(getattr(session, "directory_path", "") or "root")
        return provider.shutdown_vm(
            target=target,
            vm_name=vm_name,
            session=session,
            directory_path=directory_path,
            timeout_seconds=timeout_seconds,
        )

    def _run_script(self, script_name: str, args: list[str], *, env: dict[str, str] | None = None) -> VmActionResult:
        script_path = Path(__file__).resolve().parents[3] / "scripts" / script_name
        completed = _admin_service._run_subprocess([str(script_path), *args], capture_output=True, text=True, env=env)
        vm_name = ""
        if "--vm-name" in args:
            try:
                vm_name = args[args.index("--vm-name") + 1]
            except IndexError:
                vm_name = ""
        elif len(args) > 1:
            issue_target = args[1]
            issue_number = _admin_service._extract_issue_number(issue_target) or issue_target
            vm_name = _admin_service._resolve_vm_name(issue_number) if issue_number else ""
        message = (completed.stdout or completed.stderr or "").strip()
        return VmActionResult(
            success=completed.returncode == 0,
            vm_name=vm_name,
            action=script_name.removesuffix(".sh"),
            message=message or f"{script_name} exited with code {completed.returncode}",
        )

    def enable_agent(self, target: str, *, vm_user: str = "agent") -> VmActionResult:
        vm_name = self._resolve_vm_name_from_target(target)
        return self._run_script(
            "set_issue_agent_state.sh",
            ["--state", "enable", "--vm-name", vm_name, "--vm-user", vm_user, "--host-state-path", str(self._deps.config.host_agent_control_state_path)],
        )

    def disable_agent(self, target: str, *, vm_user: str = "agent") -> VmActionResult:
        vm_name = self._resolve_vm_name_from_target(target)
        return self._run_script(
            "set_issue_agent_state.sh",
            ["--state", "disable", "--vm-name", vm_name, "--vm-user", vm_user, "--host-state-path", str(self._deps.config.host_agent_control_state_path)],
        )

    def kill_agent(self, target: str, pid: int, *, vm_user: str = "agent") -> VmActionResult:
        vm_name = self._resolve_vm_name_from_target(target)
        if pid <= 0:
            return VmActionResult(
                success=False,
                vm_name=vm_name,
                action="kill_agent",
                message=f"Invalid agent pid: {pid}",
            )
        current_state = _admin_service._dominfo_field(vm_name, "State")
        if current_state is None:
            return VmActionResult(success=False, vm_name=vm_name, action="kill_agent", message=f"VM not found: {vm_name}")
        if current_state != "running":
            return VmActionResult(
                success=False,
                vm_name=vm_name,
                action="kill_agent",
                message=f"VM is not running: {vm_name}",
            )
        try:
            vm_ip, ssh_private_key_path = _admin_service._resolve_vm_ssh_access(vm_name, vm_user)
        except RuntimeError as exc:
            return VmActionResult(success=False, vm_name=vm_name, action="kill_agent", message=str(exc))

        remote_script = r"""
set -euo pipefail

TARGET_PID="${TARGET_PID:-}"
LOG_DIR="${HOME}/.agent-sandbox"

is_valid_agent_pid() {
  local pid="$1"
  local cmdline_path="/proc/${pid}/cmdline"
  local cmdline=""

  [[ -n "${pid}" && "${pid}" =~ ^[0-9]+$ ]] || return 1
  kill -0 "${pid}" >/dev/null 2>&1 || return 1
  [[ -r "${cmdline_path}" ]] || return 1
  cmdline="$(tr '\000' ' ' < "${cmdline_path}" 2>/dev/null || true)"
  [[ -n "${cmdline}" ]] || return 1
  [[ "${cmdline}" == *"agent_runner_exec"* || "${cmdline}" == *"agent_runner_resume_last"* ]]
}

read_state_value() {
  local state_path="$1"
  local key="$2"
  [[ -f "${state_path}" ]] || return 1
  awk -F= -v target_key="${key}" '$1 == target_key {print substr($0, index($0, "=") + 1); exit}' "${state_path}" 2>/dev/null
}

write_idle_state() {
  local state_path="$1"
  local repo_name="$2"
  local dispatch_id="$3"
  local now tmp_path

  now="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  tmp_path="${state_path}.tmp"
  {
    printf "status=idle\n"
    printf "phase=terminated_by_admin\n"
    printf "repo=%s\n" "${repo_name}"
    printf "pid=%s\n" "${TARGET_PID}"
    printf "updated_at=%s\n" "${now}"
    if [[ -n "${dispatch_id}" ]]; then
      printf "dispatch_id=%s\n" "${dispatch_id}"
    fi
    printf "exit_code=143\n"
  } > "${tmp_path}"
  mv "${tmp_path}" "${state_path}"
}

remove_matching_pid_file() {
  local pid_file="$1"
  local file_pid=""
  [[ -f "${pid_file}" ]] || return 0
  file_pid="$(cat "${pid_file}" 2>/dev/null || true)"
  if [[ "${file_pid}" == "${TARGET_PID}" ]]; then
    rm -f "${pid_file}"
  fi
}

if ! is_valid_agent_pid "${TARGET_PID}"; then
  echo "Guest agent pid is not active or not managed by agent-sandbox: ${TARGET_PID}" >&2
  exit 1
fi

matched_states=()
shopt -s nullglob
for state_path in "${LOG_DIR}"/agent-*.state "${LOG_DIR}/codex-resume-last.state"; do
  state_pid="$(read_state_value "${state_path}" "pid" || true)"
  if [[ "${state_pid}" == "${TARGET_PID}" ]]; then
    matched_states+=("${state_path}")
  fi
done

kill "${TARGET_PID}" >/dev/null 2>&1 || true
sleep 1
if kill -0 "${TARGET_PID}" >/dev/null 2>&1; then
  kill -9 "${TARGET_PID}" >/dev/null 2>&1 || true
fi

for state_path in "${matched_states[@]}"; do
  repo_name="$(read_state_value "${state_path}" "repo" || true)"
  dispatch_id="$(read_state_value "${state_path}" "dispatch_id" || true)"
  write_idle_state "${state_path}" "${repo_name}" "${dispatch_id}"
done

for pid_file in "${LOG_DIR}"/agent-*.pid "${LOG_DIR}/codex-heartbeat.pid"; do
  remove_matching_pid_file "${pid_file}"
done
shopt -u nullglob

printf 'terminated pid=%s state_files=%s\n' "${TARGET_PID}" "${#matched_states[@]}"
"""
        completed = _admin_service._run_subprocess(
            ["ssh", *_admin_service._ssh_options(vm_name, vm_ip, ssh_private_key_path), f"{vm_user}@{vm_ip}", "env", f"TARGET_PID={pid}", "bash", "-s"],
            capture_output=True,
            text=True,
            timeout=20.0,
            input=remote_script,
        )
        message = (completed.stdout or completed.stderr or "").strip()
        return VmActionResult(
            success=completed.returncode == 0,
            vm_name=vm_name,
            action="kill_agent",
            message=message or f"kill_agent exited with code {completed.returncode}",
        )

    def resume_agent(
        self,
        target: str,
        prompt: str,
        *,
        model: str | None = None,
        vm_user: str = "agent",
        dispatch_id: str | None = None,
        resume_thread_id: str | None = None,
        resume_mode: str = "new_context",
    ) -> VmActionResult:
        session_repository = self._session_repository()
        vm_name = self._resolve_vm_name_from_target(target)
        session_id: str | None = None
        directory_path: str | None = None
        manifest: Any | None = None
        effective_resume_thread_id: str | None = None
        try:
            session_id, manifest = self._resolve_session_target(target)
            resolved_model = str(model or "").strip() or self._deps.resolve_agent_model_for_session(manifest)
            directory_path = manifest.directory_path
            stored_resume_thread_id = str(manifest.metadata.get("agent_thread_id") or "").strip() or None
            effective_resume_thread_id = str(resume_thread_id or "").strip() or stored_resume_thread_id
        except ValueError as exc:
            return VmActionResult(
                success=False,
                vm_name=vm_name,
                action="resume_issue_agent",
                message=str(exc),
            )
        script_path = Path(__file__).resolve().parents[3] / "scripts" / "resume_issue_agent.sh"
        normalized_resume_mode = str(resume_mode).strip() or "new_context"
        if normalized_resume_mode not in {"same_context", "new_context"}:
            return VmActionResult(
                success=False,
                vm_name=vm_name,
                action="resume_issue_agent",
                message=f"Unsupported resume mode: {resume_mode}",
            )
        args = [
            str(script_path),
            "--prompt",
            prompt,
            "--vm-name",
            vm_name,
            "--vm-user",
            vm_user,
            "--host-control-state-path",
            str(self._deps.config.host_agent_control_state_path),
            *(["--dispatch-id", dispatch_id] if dispatch_id else []),
            *(["--resume-thread-id", effective_resume_thread_id] if effective_resume_thread_id else ["--resume-mode", normalized_resume_mode]),
        ]
        completed = _admin_service._run_subprocess(args, capture_output=True, text=True, env=os.environ | {"AGENT_MODEL": resolved_model})
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        thread_id: str | None = None
        message_lines: list[str] = []
        for line in stdout.splitlines():
            if line.startswith("thread_id="):
                candidate = line.partition("=")[2].strip()
                if candidate:
                    thread_id = candidate
                continue
            message_lines.append(line)
        message = "\n".join(line for line in message_lines if line.strip()).strip() or stderr.strip()
        resolved_thread_id = thread_id or effective_resume_thread_id
        if completed.returncode == 0 and session_id and manifest is not None and resolved_thread_id:
            updated_metadata = dict(manifest.metadata)
            updated_metadata["agent_thread_id"] = resolved_thread_id
            self._persist_session(session_id, manifest.model_copy(update={"metadata": updated_metadata}))
        if completed.returncode == 0 and dispatch_id:
            self._spawn_agent_completion_watcher(
                vm_name=vm_name,
                dispatch_id=dispatch_id,
                vm_user=vm_user,
                session_id=session_id,
                directory_path=directory_path,
            )
        return VmActionResult(
            success=completed.returncode == 0,
            vm_name=vm_name,
            action="resume_issue_agent",
            message=message or f"resume_issue_agent.sh exited with code {completed.returncode}",
            thread_id=resolved_thread_id,
        )

    def _spawn_agent_completion_watcher(
        self,
        *,
        vm_name: str,
        dispatch_id: str,
        vm_user: str,
        session_id: str | None,
        directory_path: str | None,
    ) -> None:
        watcher_cmd = [
            sys.executable,
            "-m",
            "agent_sandbox.agent_completion_watcher",
            "--runtime-dir",
            str(self._deps.config.runtime_dir),
            "--vm-name",
            vm_name,
            "--dispatch-id",
            dispatch_id,
            "--vm-user",
            vm_user,
            "--poll-interval-seconds",
            "1.0",
            "--timeout-seconds",
            "172800",
        ]
        trigger_worker_config = self._deps.read_worker_global_config("trigger-orchestrator").config
        workflow_runtime_dir = _admin_service._resolve_path_setting(
            base_dir=self._deps.project_dir,
            value=trigger_worker_config.get("runtime_dir"),
            fallback=self._deps.config.runtime_dir,
        )
        event_queue_store_path = _admin_service._resolve_path_setting(
            base_dir=self._deps.project_dir,
            value=trigger_worker_config.get("event_queue_store_path"),
            fallback=workflow_runtime_dir / "trigger-orchestrator-events.json",
        )
        workflow_event_journal = _admin_service._resolve_path_setting(
            base_dir=self._deps.project_dir,
            value=trigger_worker_config.get("workflow_event_journal_path"),
            fallback=_admin_service.workflow_event_journal_path(workflow_runtime_dir),
        )
        watcher_cmd.extend(["--event-queue-store-path", str(event_queue_store_path)])
        watcher_cmd.extend(["--workflow-event-journal-path", str(workflow_event_journal)])
        if session_id:
            watcher_cmd.extend(["--session-id", session_id])
        if directory_path:
            watcher_cmd.extend(["--directory-path", directory_path])
        subprocess.Popen(
            watcher_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            cwd=str(self._deps.project_dir),
            env=os.environ.copy(),
            text=True,
        )

    def vm_exec(
        self,
        target: str,
        *,
        command: str | list[str],
        vm_user: str = "agent",
        working_directory: str | None = None,
        environment: dict[str, str] | None = None,
        stdin: str | None = None,
        timeout_seconds: float | None = None,
        check: bool = True,
    ) -> dict[str, Any]:
        sessions = self._deps.runtime.sessions.read_all()
        provider, vm_name, session = self._provider_registry().resolve_target(
            target=target,
            sessions=sessions,
            directory_path=str(getattr(sessions.get(target), "directory_path", "") or "root"),
        )
        directory_path = str(getattr(session, "directory_path", "") or "root")
        return provider.vm_exec(
            target=target,
            vm_name=vm_name,
            session=session,
            vm_user=vm_user,
            command=command,
            working_directory=working_directory,
            environment=environment,
            stdin=stdin,
            timeout_seconds=timeout_seconds,
            check=check,
            directory_path=directory_path,
        )

    def vm_copy_files(
        self,
        target: str,
        *,
        files: list[dict[str, Any]],
        vm_user: str = "agent",
    ) -> dict[str, Any]:
        sessions = self._deps.runtime.sessions.read_all()
        provider, vm_name, session = self._provider_registry().resolve_target(
            target=target,
            sessions=sessions,
            directory_path=str(getattr(sessions.get(target), "directory_path", "") or "root"),
        )
        directory_path = str(getattr(session, "directory_path", "") or "root")
        return provider.vm_copy_files(
            target=target,
            vm_name=vm_name,
            session=session,
            files=files,
            vm_user=vm_user,
            directory_path=directory_path,
        )

    def vm_delete_files(
        self,
        target: str,
        *,
        paths: list[str],
        vm_user: str = "agent",
        recursive: bool = False,
        missing_ok: bool = True,
    ) -> dict[str, Any]:
        sessions = self._deps.runtime.sessions.read_all()
        provider, vm_name, session = self._provider_registry().resolve_target(
            target=target,
            sessions=sessions,
            directory_path=str(getattr(sessions.get(target), "directory_path", "") or "root"),
        )
        directory_path = str(getattr(session, "directory_path", "") or "root")
        return provider.vm_delete_files(
            target=target,
            vm_name=vm_name,
            session=session,
            paths=paths,
            vm_user=vm_user,
            recursive=recursive,
            missing_ok=missing_ok,
            directory_path=directory_path,
        )

    def create_issue_vm(
        self,
        *,
        issue: str,
        session_id: str | None = None,
        model: str | None = None,
        vm_user: str = "agent",
        ttl_hours: int | None = None,
        discord_channels: list[str] | None = None,
        email_mailboxes: list[str] | None = None,
        email_folders: list[str] | None = None,
        dotnet: bool = False,
        nuget: str | None = None,
        mssql: bool = False,
        mssql_init_root: str | None = None,
        playwright: bool = False,
        codex_skills: list[str] | None = None,
        directory_path: str = "root",
    ) -> VmActionResult:
        issue_input = issue
        issue_number = _admin_service._resolve_issue_number(issue_input)
        local_issue: Any | None = None
        candidate_issue_path = Path(issue_input)
        if candidate_issue_path.is_file():
            local_issue = self._deps.resolve_local_issue_record(issue_input)
            issue_number = local_issue.issue_number
            issue_input = local_issue.yaml_path
        else:
            try:
                local_issue = self._deps.resolve_local_issue_record(issue_input)
                issue_number = local_issue.issue_number
                issue_input = local_issue.yaml_path
            except ValueError as exc:
                if not str(exc).startswith("Unknown local issue identifier: "):
                    return VmActionResult(
                        success=False,
                        vm_name=f"vm-{issue_number}",
                        action="create_issue_vm",
                        message=str(exc),
                    )
        resolved_session_id = session_id or _admin_service._default_issue_session_id(issue_number)
        resolved_vm_name = f"vm-{issue_number}"
        normalized_directory_path = self._deps.normalize_directory_path(
            local_issue.directory_path if local_issue is not None and directory_path == "root" else directory_path
        )
        policy = self._deps.runtime.policy_config.resolve_for_directory(normalized_directory_path)
        session_repository = self._session_repository()
        if session_repository.get_session(resolved_session_id) is not None:
            return VmActionResult(
                success=False,
                vm_name=resolved_vm_name,
                action="create_issue_vm",
                message=f"Session already exists: {resolved_session_id}",
            )
        if mssql and not os.environ.get("MSSQL_SA_PASSWORD", "").strip():
            return VmActionResult(
                success=False,
                vm_name=resolved_vm_name,
                action="create_issue_vm",
                message="MSSQL_SA_PASSWORD must be set when --mssql is used.",
            )

        manifest = _admin_service._build_issue_session_manifest(
            policy=policy,
            session_id=resolved_session_id,
            vm_id=resolved_vm_name,
            issue_number=issue_number,
            directory_path=normalized_directory_path,
            ttl_hours=ttl_hours,
            discord_channels=discord_channels or [],
            email_mailboxes=email_mailboxes or [],
            email_folders=email_folders or [],
        )
        normalized_model = (model or "").strip() or None
        if normalized_model is not None:
            manifest.model = normalized_model
        else:
            try:
                manifest.model = self._deps.resolve_agent_model_for_session(manifest)
            except ValueError:
                manifest.model = None
        self._persist_session(resolved_session_id, manifest)

        def _issue_vm_failure(message: str) -> VmActionResult:
            try:
                preserved_vm = _admin_service._dominfo_field(resolved_vm_name, "State") is not None
            except RuntimeError:
                preserved_vm = False
            if not preserved_vm:
                session_repository.delete_session(resolved_session_id)
            detail = message
            if preserved_vm:
                detail = (
                    f"{detail}\n"
                    f"VM {resolved_vm_name} and session {resolved_session_id} were preserved for inspection."
                )
            return VmActionResult(
                success=False,
                vm_name=resolved_vm_name,
                action="create_issue_vm",
                message=detail,
            )

        try:
            root_dir = Path(__file__).resolve().parents[3]
            setup_script = root_dir / "scripts" / "setup_vm.sh"
            vm_defaults = self._deps.resolve_vm_defaults(normalized_directory_path)
            issue_setup = self._deps.resolve_issue_setup(normalized_directory_path, root_dir=root_dir)
            run_env = os.environ.copy()
            run_env.update(vm_defaults.guest_environment())
            if manifest.model:
                run_env["AGENT_MODEL"] = manifest.model
            run_env.update(
                {
                    "AGENT_SANDBOX_GIT_ROOT": issue_setup.git_root,
                    "AGENT_SANDBOX_GIT_HOST": issue_setup.git_host,
                    "AGENT_SANDBOX_GIT_USER": issue_setup.git_user,
                    "AGENT_SANDBOX_GIT_SSH_ALIAS": issue_setup.git_ssh_alias,
                    "ENABLE_GUEST_HEARTBEAT": "1" if issue_setup.enable_guest_heartbeat else "0",
                    "GUEST_HEARTBEAT_PROMPT": issue_setup.guest_heartbeat_prompt,
                    "AGENT_SANDBOX_AZURE_DEVOPS_PR_PAT_ENV": issue_setup.issue_setup_azure_devops_pr_pat_env,
                }
            )
            if issue_setup.post_exec_verification_prompt is not None:
                run_env["AGENT_POST_EXEC_VERIFICATION_PROMPT"] = issue_setup.post_exec_verification_prompt

            helper = self._deps.get_privileged_helper() if self._deps.get_use_privileged_helper() else None
            if helper is not None and not helper.is_running():
                helper.start()

            setup_args: list[str] = []
            if dotnet:
                setup_args.append("--dotnet")
            if nuget:
                setup_args.append("--nuget")
            if mssql:
                setup_args.append("--mssql")
                if mssql_init_root is not None:
                    setup_args.extend(["--mssql-init-root", mssql_init_root])
            if playwright:
                setup_args.append("--playwright")
            for skill_name in [item.strip() for item in (codex_skills or []) if item and item.strip()]:
                setup_args.extend(["--codex-skill", skill_name])
            setup_args.extend([resolved_vm_name, resolved_session_id, vm_user])
            if helper is not None:
                response = helper.call(
                    "run_script",
                    script_path=str(setup_script),
                    args=setup_args,
                    env=run_env,
                )
                message = _admin_service._combined_process_message(
                    str(response.get("stdout") or ""),
                    str(response.get("stderr") or ""),
                )
                completed = SimpleNamespace(returncode=int(response.get("returncode") or 0))
            else:
                allowed_env_keys = (
                    "SSH_PUBLIC_KEY_FILE",
                    "SSH_PRIVATE_KEY_PATH",
                    "GATEWAY_URL",
                    "LLM_BASE_URL",
                    "AGENT_MODEL",
                    "NUGET_RELAY_BASE_URL",
                    "AGENT_SANDBOX_RUNTIME_DIR",
                    "VM_STORAGE_DIR",
                    "SHARED_NUGET_CACHE_DIR",
                    "SHARED_NUGET_CACHE_TAG",
                    "SHARED_NUGET_CACHE_MODE",
                    "RAM_MB",
                    "VCPUS",
                    "DISK_SIZE_GB",
                    "VM_WAIT_IP_TIMEOUT_SECONDS",
                    "VM_WAIT_SSH_TIMEOUT_SECONDS",
                    "MSSQL_SA_PASSWORD",
                    "AGENT_SANDBOX_GIT_ROOT",
                    "AGENT_SANDBOX_GIT_HOST",
                    "AGENT_SANDBOX_GIT_USER",
                    "AGENT_SANDBOX_GIT_SSH_ALIAS",
                    "AGENT_SANDBOX_AZURE_DEVOPS_PR_PAT_ENV",
                    "AGENT_POST_EXEC_VERIFICATION_PROMPT",
                    "ENABLE_GUEST_HEARTBEAT",
                    "GUEST_HEARTBEAT_PROMPT",
                )
                launch_step = _admin_service._sudo_env_command(
                    [str(setup_script), *setup_args],
                    run_env,
                    allowed_env_keys,
                )
                completed = _admin_service._run_subprocess(launch_step, capture_output=True, text=True, env=run_env)
                message = _admin_service._combined_process_message(completed.stdout, completed.stderr)
                if completed.returncode != 0 and _admin_service._can_prompt_for_privileges():
                    launch_step = _admin_service._pkexec_env_command(
                        [str(setup_script), *setup_args],
                        run_env,
                        allowed_env_keys,
                    )
                    completed = _admin_service._run_subprocess(launch_step, capture_output=True, text=True, env=run_env)
                    message = _admin_service._combined_process_message(completed.stdout, completed.stderr)
        except Exception as exc:
            return _issue_vm_failure(str(exc) or f"Failed to create VM {resolved_vm_name}")

        if completed.returncode != 0:
            return _issue_vm_failure(message or f"{setup_script.name} exited with code {completed.returncode}")
        return VmActionResult(
            success=True,
            vm_name=resolved_vm_name,
            action="create_issue_vm",
            message=message or f"Created issue VM {resolved_vm_name}",
        )

    def create_issue_vm_from_local_issue(
        self,
        issue_identifier: str,
        *,
        model: str | None = None,
        vm_user: str = "agent",
        dotnet: bool = False,
        nuget: str | None = None,
        mssql: bool = False,
        mssql_init_root: str | None = None,
        playwright: bool = False,
        codex_skills: list[str] | None = None,
    ) -> VmActionResult:
        try:
            issue_key = self._deps.resolve_local_issue_key(issue_identifier)
        except ValueError as exc:
            return VmActionResult(
                success=False,
                vm_name=f"vm-{issue_identifier}",
                action="create_issue_vm",
                message=str(exc),
            )
        issue = self._deps.local_issue_store.get(issue_key) if issue_key is not None else None
        if issue is None:
            return VmActionResult(
                success=False,
                vm_name=f"vm-{issue_identifier}",
                action="create_issue_vm",
                message=f"Unknown local issue: {issue_identifier}",
            )
        return self.create_issue_vm(
            issue=issue.yaml_path,
            model=model,
            vm_user=vm_user,
            dotnet=dotnet,
            nuget=nuget,
            mssql=mssql,
            mssql_init_root=mssql_init_root,
            playwright=playwright,
            codex_skills=codex_skills,
            directory_path=issue.directory_path,
        )

    def create_vm(
        self,
        *,
        provider: str | None = None,
        **kwargs: Any,
    ) -> VmActionResult:
        directory_path = str(kwargs.get("directory_path") or "root")
        resolved_provider = str(provider or self._deps.resolve_vm_defaults(directory_path).provider or "libvirt").strip() or "libvirt"
        result = self._provider_registry().get(resolved_provider).create_vm(provider=resolved_provider, **kwargs)
        return result

    def clone_repo_into_vm(
        self,
        *,
        vm_name: str,
        session_id: str | None = None,
        repo_source: str,
        clone_branch: str | None = None,
        vm_user: str = "agent",
        directory_path: str = "root",
    ) -> VmActionResult:
        resolved_vm_name = vm_name.strip()
        if not resolved_vm_name:
            return VmActionResult(success=False, vm_name="", action="clone_repo_into_vm", message="VM name is required.")

        normalized_repo_source = repo_source.strip()
        if not normalized_repo_source:
            return VmActionResult(
                success=False,
                vm_name=resolved_vm_name,
                action="clone_repo_into_vm",
                message="repo_source is required.",
            )
        normalized_clone_branch = clone_branch.strip() if clone_branch else ""

        resolved_session_id, session, normalized_directory_path, error_result = self._resolve_vm_operation_context(
            vm_name=resolved_vm_name,
            session_id=session_id,
            action="clone_repo_into_vm",
            directory_path=directory_path,
        )
        if error_result is not None:
            return error_result

        root_dir = Path(__file__).resolve().parents[3]
        issue_setup = self._deps.resolve_issue_setup(normalized_directory_path, root_dir=root_dir)
        vm_ip = _admin_service._resolve_vm_ip(resolved_vm_name)

        try:
            resolved_repo_path, resolved_repo_name = self._deps.resolve_repo_source(normalized_repo_source)
            self._deps.sync_repo_into_bare_remote(
                resolved_repo_name,
                resolved_repo_path,
                issue_setup=issue_setup,
                repo_source=normalized_repo_source,
            )
            guest_repo_root = self._deps.clone_repo_into_vm(
                vm_name=resolved_vm_name,
                vm_user=vm_user,
                session_id=resolved_session_id,
                repo_name=resolved_repo_name,
                issue_setup=issue_setup,
                repo_source=normalized_repo_source,
                clone_branch=normalized_clone_branch or None,
                vm_ip=vm_ip,
            )
            guest_remote_url = f"ssh://{issue_setup.git_ssh_alias}{issue_setup.git_root}/{resolved_repo_name}.git"
        except Exception as exc:
            return VmActionResult(
                success=False,
                vm_name=resolved_vm_name,
                action="clone_repo_into_vm",
                message=f"Failed to clone repo into VM {resolved_vm_name}: {exc}",
            )

        return VmActionResult(
            success=True,
            vm_name=resolved_vm_name,
            action="clone_repo_into_vm",
            message=f"Cloned repo into {resolved_vm_name}",
            target_dir=guest_repo_root,
            guest_repo_name=resolved_repo_name,
            remote_url=guest_remote_url,
        )

    def bootstrap_python_in_vm(
        self,
        *,
        vm_name: str,
        session_id: str | None = None,
        host_checkout: str,
        target_dir: str,
        vm_user: str = "agent",
    ) -> VmActionResult:
        resolved_vm_name = vm_name.strip()
        if not resolved_vm_name:
            return VmActionResult(success=False, vm_name="", action="bootstrap_python_in_vm", message="VM name is required.")

        normalized_host_checkout = host_checkout.strip()
        if not normalized_host_checkout:
            return VmActionResult(
                success=False,
                vm_name=resolved_vm_name,
                action="bootstrap_python_in_vm",
                message="host_checkout is required.",
            )

        normalized_target_dir = target_dir.strip()
        if not normalized_target_dir:
            return VmActionResult(
                success=False,
                vm_name=resolved_vm_name,
                action="bootstrap_python_in_vm",
                message="target_dir is required.",
            )

        _, _, _, error_result = self._resolve_vm_operation_context(
            vm_name=resolved_vm_name,
            session_id=session_id,
            action="bootstrap_python_in_vm",
            directory_path="root",
        )
        if error_result is not None:
            return error_result

        python_script = Path(__file__).resolve().parents[3] / "scripts" / "bootstrap_python_vm.sh"
        try:
            completed = _admin_service._run_subprocess(
                [str(python_script), "--vm-user", vm_user, resolved_vm_name, normalized_host_checkout, normalized_target_dir],
                capture_output=True,
                text=True,
                env=os.environ | {"HOST_PYTHON_BIN": _admin_service._resolve_host_python_with_pip()},
            )
            message = _admin_service._combined_process_message(completed.stdout, completed.stderr)
            if completed.returncode != 0:
                raise RuntimeError(message or f"{python_script.name} exited with code {completed.returncode}")
        except Exception as exc:
            return VmActionResult(
                success=False,
                vm_name=resolved_vm_name,
                action="bootstrap_python_in_vm",
                message=f"Failed to bootstrap Python in VM {resolved_vm_name}: {exc}",
            )

        return VmActionResult(
            success=True,
            vm_name=resolved_vm_name,
            action="bootstrap_python_in_vm",
            message=message or f"Bootstrapped Python in {resolved_vm_name}",
        )

    def bootstrap_frontend_in_vm(
        self,
        *,
        vm_name: str,
        session_id: str | None = None,
        host_checkout: str,
        install_roots: list[str] | None,
        vm_user: str = "agent",
    ) -> VmActionResult:
        resolved_vm_name = vm_name.strip()
        if not resolved_vm_name:
            return VmActionResult(success=False, vm_name="", action="bootstrap_frontend_in_vm", message="VM name is required.")

        normalized_host_checkout = host_checkout.strip()
        if not normalized_host_checkout:
            return VmActionResult(
                success=False,
                vm_name=resolved_vm_name,
                action="bootstrap_frontend_in_vm",
                message="host_checkout is required.",
            )

        normalized_install_roots = [item.strip() for item in (install_roots or []) if item and item.strip()]
        if not normalized_install_roots:
            return VmActionResult(
                success=False,
                vm_name=resolved_vm_name,
                action="bootstrap_frontend_in_vm",
                message="install_roots is required.",
            )

        _, _, _, error_result = self._resolve_vm_operation_context(
            vm_name=resolved_vm_name,
            session_id=session_id,
            action="bootstrap_frontend_in_vm",
            directory_path="root",
        )
        if error_result is not None:
            return error_result

        frontend_script = Path(__file__).resolve().parents[3] / "scripts" / "bootstrap_frontend_deps_vm.sh"
        try:
            completed = _admin_service._run_subprocess(
                [str(frontend_script), "--vm-user", vm_user, resolved_vm_name, normalized_host_checkout, *normalized_install_roots],
                capture_output=True,
                text=True,
                env=os.environ.copy(),
            )
            message = _admin_service._combined_process_message(completed.stdout, completed.stderr)
            if completed.returncode != 0:
                raise RuntimeError(message or f"{frontend_script.name} exited with code {completed.returncode}")
        except Exception as exc:
            return VmActionResult(
                success=False,
                vm_name=resolved_vm_name,
                action="bootstrap_frontend_in_vm",
                message=f"Failed to bootstrap frontend deps in VM {resolved_vm_name}: {exc}",
            )

        return VmActionResult(
            success=True,
            vm_name=resolved_vm_name,
            action="bootstrap_frontend_in_vm",
            message=message or f"Bootstrapped frontend deps in {resolved_vm_name}",
        )

    def _resolve_vm_operation_context(
        self,
        *,
        vm_name: str,
        session_id: str | None,
        action: str,
        directory_path: str,
    ) -> tuple[str, Any | None, str, VmActionResult | None]:
        try:
            inferred_session_id, inferred_session = self._find_session_for_vm(vm_name)
        except ValueError as exc:
            return "", None, "root", VmActionResult(success=False, vm_name=vm_name, action=action, message=str(exc))

        resolved_session_id = session_id.strip() if session_id and session_id.strip() else inferred_session_id
        session = inferred_session
        if resolved_session_id:
            session = self._deps.runtime.sessions.get(resolved_session_id)
            if session is None:
                return (
                    "",
                    None,
                    "root",
                    VmActionResult(
                        success=False,
                        vm_name=vm_name,
                        action=action,
                        message=f"Unknown session: {resolved_session_id}",
                    ),
                )
        else:
            resolved_session_id = _admin_service._default_vm_session_id(vm_name)

        if session is None and _admin_service._dominfo_field(vm_name, "State") is None:
            return (
                "",
                None,
                "root",
                VmActionResult(success=False, vm_name=vm_name, action=action, message=f"VM not found: {vm_name}"),
            )

        normalized_directory_path = str(getattr(session, "directory_path", "") or "").strip() or self._deps.normalize_directory_path(directory_path)
        return resolved_session_id, session, normalized_directory_path, None

    def destroy_vm(self, target: str) -> VmActionResult:
        vm_name = target.strip()
        if not vm_name:
            return VmActionResult(success=False, vm_name="", action="destroy_vm", message="VM name is required.")
        issue_number = _admin_service._extract_issue_number(vm_name)
        if issue_number is not None and vm_name == f"vm-{issue_number}":
            return self.destroy_issue_vm(issue_number)

        root_dir = Path(__file__).resolve().parents[3]
        destroy_script = root_dir / "scripts" / "destroy_agent_vm.sh"
        helper = self._deps.get_privileged_helper() if self._deps.get_use_privileged_helper() else None
        if helper is not None and not helper.is_running():
            helper.start()
        if helper is not None:
            response = helper.call("destroy_vm", script_path=str(destroy_script), vm_name=vm_name)
            message = str(response.get("stdout") or response.get("stderr") or "").strip()
        else:
            completed = _admin_service._run_subprocess(
                ["pkexec", str(destroy_script), vm_name],
                capture_output=True,
                text=True,
            )
            message = (completed.stdout or completed.stderr or "").strip()
            if completed.returncode != 0:
                return VmActionResult(
                    success=False,
                    vm_name=vm_name,
                    action="destroy_vm",
                    message=message or f"destroy-vm exited with code {completed.returncode}",
                )

        session_repository = self._session_repository()
        session_ids = {session.session_id for session in session_repository.list_sessions_for_vm(vm_name)}
        for session_id in session_ids:
            session_repository.delete_session(session_id)
        removed_approvals = _admin_service._prune_approvals_for_sessions(self._deps.runtime, session_ids=session_ids)
        removed_pr_map_entries, removed_streams = _admin_service._prune_pr_map(self._deps.config.pr_map_path, session_ids=session_ids)
        removed_cursors, removed_feedback = _admin_service._prune_pr_feedback_store(
            self._deps.config.runtime_dir / "pr-feedback-store.json",
            streams=removed_streams,
        )
        summary = (
            f"Destroyed VM {vm_name}; "
            f"removed_sessions={len(session_ids)} "
            f"removed_pr_map_entries={removed_pr_map_entries} "
            f"removed_approvals={removed_approvals} "
            f"removed_feedback_cursors={removed_cursors} "
            f"removed_feedback_items={removed_feedback}"
        )
        if message:
            summary = f"{message}\n{summary}"
        return VmActionResult(success=True, vm_name=vm_name, action="destroy_vm", message=summary)

    def destroy_issue_vm(self, target: str) -> VmActionResult:
        issue_number = _admin_service._extract_issue_number(target) or target
        vm_name = f"vm-{issue_number}" if issue_number else target
        root_dir = Path(__file__).resolve().parents[3]
        destroy_script = root_dir / "scripts" / "destroy_agent_vm.sh"

        completed = _admin_service._run_subprocess(
            ["pkexec", str(destroy_script), vm_name],
            capture_output=True,
            text=True,
        )
        message = (completed.stdout or completed.stderr or "").strip()
        if completed.returncode != 0:
            return VmActionResult(
                success=False,
                vm_name=vm_name,
                action="destroy_issue_vm",
                message=message or f"destroy-issue-vm exited with code {completed.returncode}",
            )

        session_repository = self._session_repository()
        session_ids = {session.session_id for session in session_repository.list_sessions_for_issue(issue_number)}
        for session_id in session_ids:
            session_repository.delete_session(session_id)
        removed_approvals = _admin_service._prune_approvals_for_sessions(self._deps.runtime, session_ids=session_ids)
        removed_pr_map_entries, removed_streams = _admin_service._prune_pr_map(self._deps.config.pr_map_path, session_ids=session_ids)
        removed_cursors, removed_feedback = _admin_service._prune_pr_feedback_store(
            self._deps.config.runtime_dir / "pr-feedback-store.json",
            streams=removed_streams,
        )
        bare_repo_removed, bare_repo_path = _admin_service._remove_issue_bare_repo(
            issue_number,
            root_dir=root_dir,
            runtime_dir=self._deps.config.runtime_dir,
        )
        summary = (
            f"Destroyed issue VM {vm_name}; "
            f"removed_sessions={len(session_ids)} "
            f"removed_pr_map_entries={removed_pr_map_entries} "
            f"removed_approvals={removed_approvals} "
            f"removed_feedback_cursors={removed_cursors} "
            f"removed_feedback_items={removed_feedback} "
            f"bare_repo_removed={bare_repo_removed} "
            f"bare_repo_path={bare_repo_path}"
        )
        if message:
            summary = f"{message}\n{summary}"
        return VmActionResult(
            success=True,
            vm_name=vm_name,
            action="destroy_issue_vm",
            message=summary,
        )
