from __future__ import annotations

import json
import logging
import os
import random
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .agent_control import AgentControlStore
from .admin_service import AdminService, resolve_agent_model_for_manifest, resolve_effective_issue_setup_config
from .config import GatewayConfig, IssueOrchestratorWorkerGlobalConfig
from .directory_config import (
    DirectoryConfigResolver,
    DirectoryConfigStore,
    get_domain_base_config,
    validate_effective_domain_payload,
)
from .models import DirectoryPath, SessionManifest
from .runtime import RuntimeContext, build_runtime
from .wake_dispatch import (
    compute_next_sleep_seconds,
    is_session_in_cooldown,
    probe_agent_dispatch_state,
    set_session_cooldown,
    single_instance_lock,
)

logger = logging.getLogger(__name__)

_ORIGINATOR_ROLE_DEFAULTS = {"idea_generator", "bug_finder", "refactor_finder"}
_PRIORITIZER_ROLE_DEFAULTS = {"issue_prioritizer"}
_WORKER_ROLE_DEFAULTS = {"issue_worker"}
_VM_ROLE_FALLBACKS = {
    "idea-generator-vm": "idea_generator",
    "bug-finder-vm": "bug_finder",
    "refactor-finder-vm": "refactor_finder",
    "issue-prioritizer-vm": "issue_prioritizer",
    "issue-worker-1-vm": "issue_worker",
}


@dataclass(frozen=True)
class IssueOrchestratorCycleResult:
    resumed_sessions: int
    eligible_sessions: int
    next_sleep_seconds: float


class IssueOrchestratorWorker:
    def __init__(
        self,
        *,
        config: GatewayConfig,
        worker_config: IssueOrchestratorWorkerGlobalConfig,
        runtime: RuntimeContext,
        admin_service: AdminService,
        agent_control_store: AgentControlStore | None = None,
    ) -> None:
        self.config = config
        self.worker_config = worker_config
        self.runtime = runtime
        self.admin_service = admin_service
        self.agent_control_store = agent_control_store or AgentControlStore(config.host_agent_control_state_path)
        self.directory_config_resolver = DirectoryConfigResolver(
            admin_service.directory_admin._directories,
            base_gateway_config=config,
            config_store=DirectoryConfigStore(runtime.directories, runtime_dir=config.runtime_dir),
        )
        self.cooldown_state_path = worker_config.runtime_dir / "issue-orchestrator-session-cooldown.json"

    def run_forever(self) -> None:
        with single_instance_lock(self.worker_config.lock_path):
            while True:
                result = self.run_once()
                logger.debug(
                    "Issue orchestrator cycle complete resumed=%d eligible=%d sleep_seconds=%.3f",
                    result.resumed_sessions,
                    result.eligible_sessions,
                    result.next_sleep_seconds,
                )
                time.sleep(result.next_sleep_seconds)

    def run_once_with_lock(self) -> IssueOrchestratorCycleResult:
        with single_instance_lock(self.worker_config.lock_path):
            return self.run_once()

    def run_once(self) -> IssueOrchestratorCycleResult:
        resumed_sessions = 0
        eligible_sessions = 0
        for directory_path in self.worker_config.directories:
            automation = self._resolve_automation_config(directory_path)
            sessions = self._active_sessions_for_directory(directory_path)
            approved_count = len(
                self.admin_service.list_actionable_local_issues(
                    directory_path=directory_path,
                    states={"Approved"},
                )
            )
            new_issue_count = len(
                self.admin_service.list_actionable_local_issues(
                    directory_path=directory_path,
                    states={"New"},
                )
            )
            committed_count = len(
                self.admin_service.list_actionable_local_issues(
                    directory_path=directory_path,
                    states={"Committed"},
                )
            )
            for session in sessions:
                role = self._session_role(session)
                if role is None:
                    continue
                prompt = self._prompt_for_session(
                    session,
                    role=role,
                    automation=automation,
                    approved_count=approved_count,
                    new_issue_count=new_issue_count,
                    committed_count=committed_count,
                )
                if prompt is None:
                    continue
                eligible_sessions += 1
                if not self._session_can_resume(session):
                    continue
                if self._resume_session(session, prompt=prompt):
                    resumed_sessions += 1
                    set_session_cooldown(
                        path=self.cooldown_state_path,
                        session_id=session.session_id,
                        cooldown_seconds=self.worker_config.cooldown_seconds,
                    )

        next_sleep_seconds = compute_next_sleep_seconds(
            poll_interval_seconds=self.worker_config.poll_interval_seconds,
            jitter_seconds=self.worker_config.jitter_seconds,
            cooldown_seconds=self.worker_config.cooldown_seconds,
            discovered_feedback=0,
        )
        return IssueOrchestratorCycleResult(
            resumed_sessions=resumed_sessions,
            eligible_sessions=eligible_sessions,
            next_sleep_seconds=next_sleep_seconds,
        )

    def _resolve_automation_config(self, directory_path: str) -> dict[str, object]:
        resolution = self.directory_config_resolver.resolve_domain(
            directory_path,
            domain="automation",
            base_config=get_domain_base_config(self.config, "automation"),
            validator=lambda candidate: validate_effective_domain_payload("automation", candidate),
        )
        return resolution.effective_config

    def _active_sessions_for_directory(self, directory_path: str) -> list[SessionManifest]:
        now = datetime.now(UTC)
        normalized_directory = DirectoryPath(segments=directory_path).as_posix()
        sessions = [
            session
            for session in self.runtime.session_repository.list_active_sessions(now=now)
            if session.directory_path == normalized_directory and session.expires_at > now
        ]
        sessions.sort(key=lambda session: (session.session_id, session.vm_id or ""))
        return sessions

    def _session_role(self, session: SessionManifest) -> str | None:
        if isinstance(session.metadata, dict):
            role = str(session.metadata.get("automation_role") or "").strip()
            if role:
                return role
        if session.vm_id:
            return _VM_ROLE_FALLBACKS.get(session.vm_id)
        return None

    def _prompt_for_session(
        self,
        session: SessionManifest,
        *,
        role: str,
        automation: dict[str, object],
        approved_count: int,
        new_issue_count: int,
        committed_count: int,
    ) -> str | None:
        originator_roles = set(automation.get("originator_roles") or []) or _ORIGINATOR_ROLE_DEFAULTS
        prioritizer_roles = set(automation.get("prioritizer_roles") or []) or _PRIORITIZER_ROLE_DEFAULTS
        worker_roles = set(automation.get("worker_roles") or []) or _WORKER_ROLE_DEFAULTS
        approved_issue_cap = int(automation.get("approved_issue_cap") or 10)

        prelude = (
            f"Before acting, call the list_issues MCP tool with session_id={session.session_id} to inspect the current "
            "host-backed issues in this directory and avoid duplicating existing work. Once you know a specific issue "
            "identifier, prefer the get_issue MCP tool to inspect that issue directly across any state. If you create "
            f"an issue, you must use the create_local_issue MCP tool with session_id={session.session_id}. Do not use "
            "the local CLI to list or create issues. Do not write issue files directly under runtime/. Work only within "
            "the repository already cloned in the VM."
        )

        if role in originator_roles:
            if approved_count >= approved_issue_cap:
                return None
            if role == "idea_generator":
                return (
                    f"{prelude} Search the repo as an agent control plane and identify exactly one new high-impact "
                    "feature that would materially improve it. Do not suggest a bug fix or a refactor. "
                    "Create exactly one new local issue if and only if it is not already represented."
                )
            if role == "bug_finder":
                return (
                    f"{prelude} Search the repo for exactly one real regression or undiscovered bug. "
                    "Do not suggest a feature or refactor. Create exactly one new local issue if and only if "
                    "the bug is not already represented."
                )
            if role == "refactor_finder":
                return (
                    f"{prelude} Search the repo only for duplicated settings, inefficiencies, overcomplicated code, "
                    "or dead code. Do not suggest new features. Do not report actual bugs. "
                    "Create exactly one new local issue if and only if the refactor opportunity is not already represented."
                )
            return None

        if role in prioritizer_roles:
            if new_issue_count <= 0:
                return None
            return (
                f"{prelude} Process all issues currently in state New. For each one, evaluate it against the current repo. "
                f"Call approve_issue or reject_issue with session_id={session.session_id}. "
                "If it is underspecified, low-value, or factually wrong, reject it with a concise reason. "
                "If it is worthwhile, approve it with a priority of P0, P1, P2, or P3."
            )

        if role in worker_roles:
            if committed_count <= 0:
                return None
            return (
                f"Claim work by calling claim_next_issue with session_id={session.session_id} first. If no issue is returned, stop. "
                "If an issue is returned, work only on that issue. After implementing it and pushing your changes, "
                f"call complete_issue with session_id={session.session_id} and a short completion summary."
            )

        return None

    def _session_can_resume(self, session: SessionManifest) -> bool:
        if not session.vm_id:
            return False
        if self.agent_control_store.is_disabled(session.vm_id):
            return False
        if is_session_in_cooldown(path=self.cooldown_state_path, session_id=session.session_id):
            return False
        dispatch_state = probe_agent_dispatch_state(
            session_id=session.session_id,
            vm_id=session.vm_id,
            script_path=self.worker_config.busy_check_script_path,
        )
        return dispatch_state not in {"busy", "unavailable"}

    def _resume_session(self, session: SessionManifest, *, prompt: str) -> bool:
        if not session.vm_id:
            return False
        script_path = self.worker_config.resume_script_path
        if not script_path.exists():
            logger.warning("Issue orchestrator resume script missing path=%s", script_path)
            return False
        cmd = [
            str(script_path),
            "--prompt",
            prompt,
            "--vm-name",
            session.vm_id,
            "--session-id",
            session.session_id,
            "--vm-user",
            self.worker_config.vm_user,
            "--worker-mode",
            "--dispatch-only",
            "--host-state-file",
            str(self.worker_config.wake_state_path),
        ]
        if getattr(self.config, "host_agent_control_state_path", None) is not None:
            cmd.extend(["--host-control-state-path", str(self.config.host_agent_control_state_path)])
        run_kwargs = {
            "check": False,
            "capture_output": True,
            "text": True,
            "timeout": self.worker_config.resume_timeout_seconds,
        }
        agent_model = self._resolve_agent_model_for_session(session)
        if agent_model:
            run_kwargs["env"] = os.environ | {"AGENT_MODEL": agent_model}
        try:
            completed = subprocess.run(
                cmd,
                **run_kwargs,
            )
        except (OSError, subprocess.TimeoutExpired):
            logger.warning(
                "Issue orchestrator failed to resume session_id=%s vm_id=%s",
                session.session_id,
                session.vm_id,
                exc_info=True,
            )
            return False
        if completed.returncode != 0:
            logger.warning(
                "Issue orchestrator wake failed session_id=%s vm_id=%s exit_code=%s stdout=%s stderr=%s",
                session.session_id,
                session.vm_id,
                completed.returncode,
                (completed.stdout or "").strip(),
                (completed.stderr or "").strip(),
            )
            return False
        logger.info("Issue orchestrator wake dispatched session_id=%s vm_id=%s", session.session_id, session.vm_id)
        return True

    def _resolve_agent_model_for_session(self, session: SessionManifest) -> str | None:
        runtime = getattr(self, "runtime", None)
        if runtime is None or not hasattr(runtime, "directories"):
            session_model = str(getattr(session, "model", "") or "").strip()
            if session_model:
                return session_model
            return os.environ.get("AGENT_MODEL") or None
        try:
            issue_setup = resolve_effective_issue_setup_config(
                config=self.config,
                runtime=runtime,
                directory_path=session.directory_path,
                root_dir=Path(__file__).resolve().parents[2],
            )
            return resolve_agent_model_for_manifest(session, issue_setup=issue_setup)
        except ValueError as exc:
            logger.warning(
                "Cannot resolve AGENT_MODEL for issue orchestrator wake session_id=%s vm_id=%s error=%s",
                session.session_id,
                session.vm_id,
                exc,
            )
            session_model = str(getattr(session, "model", "") or "").strip()
            if session_model:
                return session_model
            return os.environ.get("AGENT_MODEL") or None


def _worker_global_config_path(project_dir: Path) -> Path:
    return project_dir / "config" / "issue-orchestrator" / "config.json"


def _load_issue_orchestrator_worker_global_config(
    project_dir: Path,
    *,
    base_config: GatewayConfig | None = None,
) -> IssueOrchestratorWorkerGlobalConfig:
    base_payload = IssueOrchestratorWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": (base_config.runtime_dir if base_config is not None else Path("runtime")),
        }
    ).model_dump(mode="json")
    path = _worker_global_config_path(project_dir)
    if not path.exists():
        return IssueOrchestratorWorkerGlobalConfig.model_validate(base_payload)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"worker config file {path} must contain a JSON object")
    return IssueOrchestratorWorkerGlobalConfig.model_validate({**base_payload, **raw})


def build_issue_orchestrator_worker(
    config: GatewayConfig,
    *,
    project_dir: Path | None = None,
) -> IssueOrchestratorWorker:
    resolved_project_dir = Path(project_dir) if project_dir is not None else Path.cwd()
    worker_config = _load_issue_orchestrator_worker_global_config(resolved_project_dir, base_config=config)
    effective_config = GatewayConfig.model_validate(
        {
            **config.model_dump(mode="json"),
            "runtime_dir": worker_config.runtime_dir,
        }
    )
    runtime = build_runtime(effective_config)
    admin_service = AdminService(effective_config.runtime_dir)
    return IssueOrchestratorWorker(
        config=effective_config,
        worker_config=worker_config,
        runtime=runtime,
        admin_service=admin_service,
        agent_control_store=AgentControlStore(effective_config.host_agent_control_state_path),
    )


def main() -> None:
    import argparse

    from .bootstrap import load_runtime_gateway_config

    parser = argparse.ArgumentParser(description="Run the issue orchestrator worker.")
    parser.add_argument("--runtime-dir", default="runtime")
    parser.add_argument("--log-level", default=os.environ.get("ISSUE_ORCHESTRATOR_LOG_LEVEL", "INFO"))
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    logging_level = getattr(logging, str(args.log_level).upper(), None)
    if not isinstance(logging_level, int):
        raise SystemExit(f"Invalid --log-level value: {args.log_level}")
    logging.basicConfig(level=logging_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    runtime_dir = Path(args.runtime_dir).expanduser().resolve()
    config = load_runtime_gateway_config(runtime_dir=runtime_dir)
    worker = build_issue_orchestrator_worker(config, project_dir=runtime_dir.parent)
    if args.once:
        result = worker.run_once_with_lock()
        print(
            f"Issue orchestrator cycle complete resumed={result.resumed_sessions} "
            f"eligible={result.eligible_sessions} sleep_seconds={result.next_sleep_seconds:.3f}"
        )
        return
    worker.run_forever()
