from __future__ import annotations

from evander.admin.services.vm_agent.common import (
    Any,
    VmActionResult,
    _agent_provider_from_session,
    _is_synthetic_chat_assistant_thread_id,
    _source_project_dir,
    ensure_session_not_expired,
    os,
    process_helpers,
    subprocess,
    sys,
)


class VmAgentResumeMixin:
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
        persist_thread_id: bool = True,
        mcp_gateway_url: str | None = None,
    ) -> VmActionResult:
        container_session = None
        if getattr(self.runtime, "session_repository", None) is not None:
            try:
                container_session, _container_record = self._resolve_container_agent_session(
                    target,
                    require_session=False,
                )
            except ValueError as exc:
                if str(exc).startswith("Ambiguous container worker session target"):
                    raise
                container_session = None
        if container_session is not None and self._is_container_session(container_session):
            try:
                container_session = self._reconcile_managed_git_remote_before_dispatch(
                    session_id=container_session.session_id,
                    session=container_session,
                    backend="container_worker_vm",
                    vm_name=None,
                    vm_user=vm_user,
                )
            except RuntimeError as exc:
                return VmActionResult(
                    success=False,
                    vm_name=self._container_session_target_name(container_session),
                    action="resume_issue_agent",
                    message=str(exc),
                    provider="container_worker_vm",
                )
            response = self._agent_prompt_dispatch_port().prompt_admin_agent(
                container_session.session_id,
                backend="container_worker_vm",
                payload={
                    "prompt": prompt,
                    "model": model,
                    "vm_user": vm_user,
                    "dispatch_id": dispatch_id,
                    "resume_thread_id": resume_thread_id,
                    "resume_mode": resume_mode,
                },
            )
            return VmActionResult(
                success=bool(response.get("success", True)),
                vm_name=str(
                    response.get("target_name")
                    or self._container_session_target_name(container_session)
                ),
                action=str(response.get("action") or "resume_issue_agent"),
                message=str(response.get("message") or ""),
                provider="container_worker_vm",
                thread_id=str(response.get("thread_id") or "") or None,
            )

        vm_name = self._resolve_vm_name_from_target(target)
        session_id: str | None = None
        directory_path: str | None = None
        manifest: Any | None = None
        effective_resume_thread_id: str | None = None
        web_search_mode = "disabled"
        agent_provider: str | None = None
        try:
            session_id, manifest = self._resolve_session_target(target)
            ensure_session_not_expired(manifest)
            resolved_model = str(model or "").strip() or self._deps.resolve_agent_model_for_session(
                manifest
            )
            directory_path = manifest.directory_path
            web_search_mode = (
                "live" if bool(getattr(manifest, "web_search_enabled", False)) else "disabled"
            )
            effective_resume_thread_id = str(resume_thread_id or "").strip() or None
            if _is_synthetic_chat_assistant_thread_id(effective_resume_thread_id):
                effective_resume_thread_id = None
            agent_provider = _agent_provider_from_session(manifest)
            manifest = self._reconcile_managed_git_remote_before_dispatch(
                session_id=session_id,
                session=manifest,
                backend="libvirt_vm",
                vm_name=vm_name,
                vm_user=vm_user,
            )
        except ValueError as exc:
            return VmActionResult(
                success=False,
                vm_name=vm_name,
                action="resume_issue_agent",
                message=str(exc),
            )
        except RuntimeError as exc:
            return VmActionResult(
                success=False,
                vm_name=vm_name,
                action="resume_issue_agent",
                message=str(exc),
            )
        script_path = _source_project_dir() / "scripts" / "resume_issue_agent.sh"
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
            *(
                ["--resume-thread-id", effective_resume_thread_id]
                if effective_resume_thread_id
                else ["--resume-mode", normalized_resume_mode]
            ),
            *(["--mcp-gateway-url", mcp_gateway_url] if mcp_gateway_url else []),
        ]
        try:
            resume_timeout_seconds = max(
                1.0,
                float(os.environ.get("EVANDER_ADMIN_RESUME_TIMEOUT_SECONDS", "120") or "120"),
            )
        except ValueError:
            resume_timeout_seconds = 120.0
        resume_env = os.environ | {
            "AGENT_MODEL": resolved_model,
            "AGENT_WEB_SEARCH_MODE": web_search_mode,
        }
        if agent_provider:
            resume_env["AGENT_PROVIDER"] = agent_provider
        try:
            completed = process_helpers.run_subprocess(
                args,
                capture_output=True,
                text=True,
                env=resume_env,
                timeout=resume_timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return VmActionResult(
                success=False,
                vm_name=vm_name,
                action="resume_issue_agent",
                message=f"resume_issue_agent.sh timed out after {resume_timeout_seconds:g}s",
            )
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
        message = (
            "\n".join(line for line in message_lines if line.strip()).strip() or stderr.strip()
        )
        resolved_thread_id = thread_id or effective_resume_thread_id
        if (
            completed.returncode == 0
            and persist_thread_id
            and session_id
            and manifest is not None
            and resolved_thread_id
        ):
            updated_metadata = dict(manifest.metadata)
            updated_metadata["agent_thread_id"] = resolved_thread_id
            self._persist_session(
                session_id, manifest.model_copy(update={"metadata": updated_metadata})
            )
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
            "evander.daemons.agent_completion_watcher",
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
        trigger_worker_config = self._deps.workflow_config.read_worker_global_config(
            "trigger-orchestrator"
        ).config
        workflow_runtime_dir = process_helpers.resolve_path_setting(
            base_dir=self._deps.project_dir,
            value=trigger_worker_config.get("runtime_dir"),
            fallback=self._deps.config.runtime_dir,
        )
        effect_lock_path = process_helpers.resolve_path_setting(
            base_dir=self._deps.project_dir,
            value=trigger_worker_config.get("effect_lock_path"),
            fallback=workflow_runtime_dir / "effect-orchestrator.lock",
        )
        orchestration_store_path = process_helpers.resolve_path_setting(
            base_dir=self._deps.project_dir,
            value=trigger_worker_config.get("orchestration_store_path"),
            fallback=workflow_runtime_dir / "orchestration.db",
        )
        orchestration_retention_days = trigger_worker_config.get("orchestration_retention_days")
        watcher_cmd.extend(["--effect-lock-path", str(effect_lock_path)])
        watcher_cmd.extend(["--orchestration-store-path", str(orchestration_store_path)])
        if orchestration_retention_days is not None:
            watcher_cmd.extend(
                ["--orchestration-retention-days", str(orchestration_retention_days)]
            )
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

    spawn_agent_completion_watcher = _spawn_agent_completion_watcher
