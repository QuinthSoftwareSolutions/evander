from __future__ import annotations

from evander.orchestration.agent_prompt_activity import AgentPromptActivityRuntime

from evander.admin.services.vm_agent.common import (
    AgentEnqueuePromptEffectDefinition,
    Any,
    EffectProviderFactory,
    Mapping,
    Path,
    UTC,
    datetime,
    hashlib,
    timedelta,
)


class VmAgentPromptMixin:
    def enqueue_agent_prompt(
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
    ) -> Any:
        del persist_thread_id, mcp_gateway_url
        backend = "libvirt_vm"
        try:
            session, _record = self._resolve_container_agent_session(
                target,
                require_session=False,
            )
        except ValueError as exc:
            if str(exc).startswith("Ambiguous container worker session target"):
                raise
            session = None
        if session is not None and self._is_container_session(session):
            backend = "container_worker_vm"
        response = self.prompt_admin_agent(
            target,
            backend=backend,
            payload={
                "prompt": prompt,
                "model": model,
                "vm_user": vm_user,
                "dispatch_id": dispatch_id,
                "resume_thread_id": resume_thread_id,
                "resume_mode": resume_mode,
            },
        )
        from evander.models import VmActionResult

        return VmActionResult(
            success=bool(response.get("success", True)),
            vm_name=str(response.get("target_name") or target),
            action=str(response.get("action") or "prompt-agent"),
            message=str(response.get("message") or ""),
            provider=backend,
            provider_details=dict(response),
            thread_id=str(response.get("thread_id") or "").strip() or None,
            queued=bool(response.get("queued")),
            queue_key=str(response.get("queue_key") or "").strip() or None,
            queue_reason=str(response.get("queue_reason") or "").strip() or None,
            dispatch_id=str(response.get("dispatch_id") or dispatch_id or "").strip() or None,
            session_id=str(response.get("session_id") or "").strip() or None,
            model=str(response.get("model") or model or "").strip() or None,
            model_provider_mode=(
                str(response.get("model_provider_mode") or "").strip() or None
            ),
            local_inference_capacity_enabled=bool(
                response.get("local_inference_capacity_enabled")
            ),
            local_inference_active_count=int(
                response.get("local_inference_active_count") or 0
            ),
            local_inference_max_concurrency=(
                int(response["local_inference_max_concurrency"])
                if response.get("local_inference_max_concurrency") is not None
                else None
            ),
            local_inference_slot_id=(
                str(response.get("local_inference_slot_id") or "").strip() or None
            ),
        )

    def prompt_admin_agent(
        self,
        agent_id: str,
        *,
        backend: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        prompt = self._agent_optional_string(payload, "prompt")
        if not prompt:
            raise ValueError("prompt-agent requires a non-empty prompt")
        model = self._agent_optional_string(payload, "model")
        dispatch_id = self._agent_optional_string(payload, "dispatch_id")
        resume_thread_id = self._agent_optional_string(payload, "resume_thread_id")
        resume_mode = self._agent_optional_string(payload, "resume_mode")
        vm_user = self._agent_optional_string(payload, "vm_user") or "agent"

        if backend == "libvirt_vm":
            session = self._resolve_optional_vm_agent_session(agent_id)
            session_id = session.session_id if session is not None else None
            target_name = (
                str((session.vm_id if session is not None else None) or agent_id).strip()
                or agent_id
            )
            directory_path = (
                str((session.directory_path if session is not None else None) or "root").strip()
                or "root"
            )
            agent_response_id = target_name
        elif backend == "container_worker_vm":
            session, record = self._resolve_container_agent_session(agent_id, require_session=True)
            if session is None:
                raise ValueError(f"Unknown container worker agent target: {agent_id}")
            del record
            session_id = session.session_id
            target_name = self._container_session_target_name(session)
            directory_path = (
                str(getattr(session, "directory_path", None) or "root").strip() or "root"
            )
            agent_response_id = session_id
        else:
            raise ValueError(f"Unknown agent backend: {backend}")

        if session is not None and session_id is not None:
            session = self._reconcile_managed_git_remote_before_dispatch(
                session_id=session_id,
                session=session,
                backend=backend,
                vm_name=target_name if backend == "libvirt_vm" else None,
                vm_user=vm_user,
            )

        target = session_id or target_name
        effect_id = f"admin-agent-prompt:{backend}:{target}"
        queue_key = (
            f"{effect_id}:dispatch:{dispatch_id}"
            if dispatch_id is not None
            else effect_id
        )
        effect = AgentEnqueuePromptEffectDefinition(
            id=effect_id,
            display_name="Admin prompt agent",
            type="effect:agent:enqueue-prompt",
            target=target,
            session_id=session_id,
            vm_name=target_name,
            prompt=prompt,
            model=model,
            resume_thread_id=resume_thread_id,
            resume_mode=resume_mode,
            dispatch_id=dispatch_id,
            vm_user=vm_user,
            queue_key=queue_key,
        )
        provider = EffectProviderFactory(
            provider_config_resolver=self.runtime.provider_config,
            admin_service=self,
            agent_prompt_activity=self._direct_agent_prompt_activity(),
        ).build(effect, directory_path=directory_path)
        dispatch_started_at = datetime.now(UTC)
        try:
            result = provider.execute()
        except RuntimeError as exc:
            queued_result = self._queued_admin_prompt_result_after_dispatch_error(
                queue_key=queue_key,
                target_name=target_name,
                session_id=session_id,
                backend=backend,
                attempted_at=dispatch_started_at,
                error=exc,
            )
            if queued_result is None:
                raise
            result = queued_result
        if backend == "libvirt_vm" and not bool(result.get("queued")):
            self._record_direct_libvirt_admin_prompt_execution(
                effect_id=effect_id,
                target=target,
                target_name=target_name,
                session_id=session_id,
                prompt=prompt,
                resume_thread_id=resume_thread_id,
                resume_mode=resume_mode,
                vm_user=vm_user,
                directory_path=directory_path,
                queue_key=queue_key,
                result=result,
            )
        if (
            backend == "container_worker_vm"
            and not bool(result.get("queued"))
            and str(result.get("output_mode") or "").strip() == "on_completion"
        ):
            self._record_direct_container_admin_prompt_execution(
                effect_id=effect_id,
                target=target,
                target_name=target_name,
                session_id=session_id,
                prompt=prompt,
                resume_thread_id=resume_thread_id,
                vm_user=vm_user,
                directory_path=directory_path,
                result=result,
            )
        queued = bool(result.get("queued"))
        message = str(result.get("message") or "").strip()
        if not message:
            message = (
                f"Queued prompt for {target_name}"
                if queued
                else f"Dispatched prompt to {target_name}"
            )
        response = dict(result)
        response.update(
            {
                "success": bool(result.get("success", True)),
                "agent_id": agent_response_id,
                "backend": backend,
                "target_name": target_name,
                "action": "prompt-agent",
                "message": message,
                "status": "queued" if queued else "dispatched",
                "session_id": session_id,
            }
        )
        return response

    def _queued_admin_prompt_result_after_dispatch_error(
        self,
        *,
        queue_key: str,
        target_name: str,
        session_id: str | None,
        backend: str,
        attempted_at: datetime,
        error: RuntimeError,
    ) -> dict[str, Any] | None:
        from evander.messaging.agent_prompt_queue import AgentPromptQueueStore

        queued = AgentPromptQueueStore(self.config.runtime_dir / "queued-agent-prompts.json").get(
            queue_key
        )
        if queued is None:
            return None
        queued_updated_at = queued.updated_at
        if queued_updated_at.tzinfo is None:
            queued_updated_at = queued_updated_at.replace(tzinfo=UTC)
        if queued_updated_at < attempted_at - timedelta(seconds=1):
            return None
        queued_session_id = str(queued.session_id or "").strip() or session_id
        queued_target_name = str(queued.vm_name or "").strip() or target_name
        return {
            "success": True,
            "queued": True,
            "queue_key": queued.queue_key,
            "vm_name": queued_target_name,
            "session_id": queued_session_id,
            "coalesced_count": queued.coalesced_count,
            "execution_backend": backend,
            "message": f"Queued prompt for {queued_target_name}",
            "dispatch_error": str(error),
        }

    def _direct_agent_prompt_activity(self) -> AgentPromptActivityRuntime:
        return AgentPromptActivityRuntime(
            runtime_dir=self.config.runtime_dir,
            execution_store=self._direct_agent_prompt_execution_store(),
        )

    def _direct_agent_prompt_execution_store(self) -> Any:
        from evander.daemons.effect_orchestrator import (
            JsonExecutionStore,
            SqliteExecutionStore,
        )

        stores = self._workflow_run_store_paths()
        if self._workflow_uses_sqlite_store(stores):
            return SqliteExecutionStore(Path(stores["orchestration_store_path"]))
        return JsonExecutionStore(Path(stores["effect_execution_store_path"]))

    def _record_direct_libvirt_admin_prompt_execution(
        self,
        *,
        effect_id: str,
        target: str,
        target_name: str,
        session_id: str | None,
        prompt: str,
        resume_thread_id: str | None,
        resume_mode: str | None,
        vm_user: str,
        directory_path: str,
        queue_key: str,
        result: Mapping[str, Any],
    ) -> None:
        dispatch_id = str(result.get("dispatch_id") or "").strip()
        thread_id = str(result.get("thread_id") or "").strip()
        lock_metadata = {
            key: str(result.get(key) or "").strip()
            for key in ("prompt_lock_key", "prompt_lock_owner_id")
            if str(result.get(key) or "").strip()
        }
        if not dispatch_id or not lock_metadata:
            raise RuntimeError(
                "prompt-agent libvirt dispatch did not include execution tracking identifiers"
            )

        from evander.daemons.effect_orchestrator import EffectExecutionRecord

        agent_key = session_id or target_name
        event_id = f"admin-agent-prompt:{agent_key}:{dispatch_id}"
        idempotency_key = f"{event_id}::{effect_id}"
        execution_store = self._direct_agent_prompt_execution_store()
        if execution_store.find_execution_by_idempotency_key(idempotency_key) is not None:
            return

        resolved_config = {
            "target": target,
            "vm_name": target_name,
            "session_id": session_id,
            "prompt": prompt,
            "model": result.get("model"),
            "vm_user": vm_user,
            "resume_thread_id": resume_thread_id,
            "resume_mode": resume_mode,
            "directory_path": directory_path,
            "execution_backend": "libvirt_vm",
            "queue_key": queue_key,
            **lock_metadata,
        }
        input_payload = {
            "target": target,
            "vm_name": target_name,
            "session_id": session_id,
            "prompt": prompt,
            "model": result.get("model"),
            "vm_user": vm_user,
            "resume_thread_id": resume_thread_id,
            "resume_mode": resume_mode,
            "queue_key": queue_key,
            **lock_metadata,
        }
        outputs = dict(result)
        outputs.update(
            {
                "dispatch_id": dispatch_id,
                "success": True,
                "queued": False,
                "vm_name": target_name,
                "session_id": session_id,
                "execution_backend": "libvirt_vm",
                "queue_key": queue_key,
                **({"thread_id": thread_id} if thread_id else {}),
                **lock_metadata,
            }
        )
        now = datetime.now(UTC)
        execution_store.record_execution(
            EffectExecutionRecord(
                execution_id=(
                    f"exec-{hashlib.sha1(idempotency_key.encode('utf-8')).hexdigest()[:16]}"
                ),
                idempotency_key=idempotency_key,
                event_id=event_id,
                trace_id=f"admin-agent-prompt:{agent_key}",
                source_node_id=effect_id,
                source_trigger_type="admin:agent:prompt",
                effect_id=effect_id,
                effect_type="effect:agent:enqueue-prompt",
                status="waiting",
                started_at=now,
                finished_at=now,
                resolved_config_hash=self._stable_json_revision(resolved_config),
                resolved_config=resolved_config,
                input_payload=input_payload,
                outputs=outputs,
            )
        )

    def _record_direct_container_admin_prompt_execution(
        self,
        *,
        effect_id: str,
        target: str,
        target_name: str,
        session_id: str | None,
        prompt: str,
        resume_thread_id: str | None,
        vm_user: str,
        directory_path: str,
        result: Mapping[str, Any],
    ) -> None:
        dispatch_id = str(result.get("dispatch_id") or "").strip()
        worker_execution_id = str(result.get("execution_id") or "").strip()
        if not dispatch_id or not worker_execution_id or not session_id:
            raise RuntimeError(
                "prompt-agent container dispatch did not include execution tracking identifiers"
            )

        from evander.daemons.effect_orchestrator import (
            EffectExecutionRecord,
            JsonExecutionStore,
            SqliteExecutionStore,
        )

        event_id = f"admin-agent-prompt:{session_id}:{dispatch_id}"
        idempotency_key = f"{event_id}::{effect_id}"
        stores = self._workflow_run_store_paths()
        if self._workflow_uses_sqlite_store(stores):
            execution_store = SqliteExecutionStore(Path(stores["orchestration_store_path"]))
        else:
            execution_store = JsonExecutionStore(Path(stores["effect_execution_store_path"]))
        if execution_store.find_execution_by_idempotency_key(idempotency_key) is not None:
            return

        resolved_config = {
            "target": target,
            "vm_name": target_name,
            "session_id": session_id,
            "prompt": prompt,
            "model": result.get("model"),
            "vm_user": vm_user,
            "resume_thread_id": resume_thread_id,
            "directory_path": directory_path,
            "execution_backend": "container_worker_vm",
            "queue_key": effect_id,
        }
        input_payload = {
            "target": target,
            "vm_name": target_name,
            "session_id": session_id,
            "prompt": prompt,
            "model": result.get("model"),
            "vm_user": vm_user,
            "resume_thread_id": resume_thread_id,
            "queue_key": effect_id,
        }
        outputs = dict(result)
        outputs.update(
            {
                "dispatch_id": dispatch_id,
                "thread_id": str(result.get("thread_id") or resume_thread_id or dispatch_id),
                "success": True,
                "queued": False,
                "output_mode": "on_completion",
                "vm_name": target_name,
                "session_id": session_id,
                "execution_backend": "container_worker_vm",
                "execution_id": worker_execution_id,
                "queue_key": effect_id,
            }
        )
        now = datetime.now(UTC)
        execution_store.record_execution(
            EffectExecutionRecord(
                execution_id=(
                    f"exec-{hashlib.sha1(idempotency_key.encode('utf-8')).hexdigest()[:16]}"
                ),
                idempotency_key=idempotency_key,
                event_id=event_id,
                trace_id=f"admin-agent-prompt:{session_id}",
                source_node_id=effect_id,
                source_trigger_type="admin:agent:prompt",
                effect_id=effect_id,
                effect_type="effect:agent:enqueue-prompt",
                status="waiting",
                started_at=now,
                finished_at=now,
                resolved_config_hash=self._stable_json_revision(resolved_config),
                resolved_config=resolved_config,
                input_payload=input_payload,
                outputs=outputs,
            )
        )

    queued_admin_prompt_result_after_dispatch_error = (
        _queued_admin_prompt_result_after_dispatch_error
    )
    record_direct_container_admin_prompt_execution = _record_direct_container_admin_prompt_execution
