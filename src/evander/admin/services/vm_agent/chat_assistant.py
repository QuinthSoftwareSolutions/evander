from __future__ import annotations

from evander.admin.services.vm_agent.common import (
    Any,
    ExecutionBackendKind,
    SessionManifest,
    UTC,
    _assistant_allowed_tools,
    _assistant_capability_profile,
    _assistant_home_file_specs,
    _assistant_mutation_policy,
    _chat_assistant_vm_name,
    _chat_thread_id_from_queue_key,
    _chat_thread_mapping_key,
    _is_synthetic_chat_assistant_thread_id,
    _safe_chat_assistant_identifier,
    _source_project_dir,
    as_session_manifest_repository,
    datetime,
    logging,
    os,
    process_helpers,
    session_helpers,
    timedelta,
    vm_helpers,
)


class VmAgentChatAssistantMixin:
    def _session_repository(self):
        runtime = self._deps.runtime
        return as_session_manifest_repository(runtime.session_repository)

    def _resolve_vm_name_from_target(self, target: str) -> str:
        repository = self._session_repository()
        try:
            resolved = repository.resolve_vm_name_from_target(target)
        except Exception:
            sessions = repository.read_all()
            return session_helpers.resolve_vm_name_from_target(target, sessions)
        normalized_target = str(target or "").strip()
        if resolved and resolved != normalized_target:
            return resolved
        sessions = repository.read_all()
        return session_helpers.resolve_vm_name_from_target(target, sessions)

    def resolve_chat_assistant_session(
        self,
        *,
        user_id: str,
        backend: str,
        assistant_scope: str = "chat_assistant",
        settings: dict[str, Any] | None = None,
        thread_id: str | None = None,
        user_message_id: str | None = None,
    ) -> dict[str, str]:
        normalized_backend = str(backend or "libvirt_vm").strip() or "libvirt_vm"
        normalized_user_id = str(user_id or "anonymous").strip() or "anonymous"
        scope = str(assistant_scope or "chat_assistant").strip() or "chat_assistant"
        safe_user_id = _safe_chat_assistant_identifier(normalized_user_id)

        mapping_user_id = normalized_user_id
        existing = self._assistant_session_repo.get_mapping(
            user_id=mapping_user_id,
            backend=normalized_backend,
            assistant_scope=scope,
        )
        if existing is not None:
            existing_chat_thread_id = str(existing.get("thread_id") or "").strip()
            requested_chat_thread_id = str(thread_id or "").strip()
            chat_thread_changed = (
                bool(existing_chat_thread_id)
                and bool(requested_chat_thread_id)
                and existing_chat_thread_id != requested_chat_thread_id
            )
            if chat_thread_changed:
                agent_thread_id = ""
                self._assistant_session_repo.upsert_mapping(
                    user_id=mapping_user_id,
                    backend=normalized_backend,
                    assistant_scope=scope,
                    session_id=existing["session_id"],
                    agent_thread_id="",
                    thread_id=requested_chat_thread_id,
                )
                self._clear_stored_chat_assistant_thread_id(existing["session_id"])
            else:
                stored_agent_thread_id = (
                    self._stored_chat_assistant_thread_id(existing["session_id"])
                    if normalized_backend == ExecutionBackendKind.CONTAINER_WORKER_VM.value
                    else ""
                )
                existing_agent_thread_id = existing.get("agent_thread_id") or ""
                if _is_synthetic_chat_assistant_thread_id(existing_agent_thread_id):
                    existing_agent_thread_id = ""
                agent_thread_id = stored_agent_thread_id or existing_agent_thread_id
            self._ensure_chat_assistant_manifest(
                session_id=existing["session_id"],
                backend=normalized_backend,
                user_id=normalized_user_id,
                safe_user_id=safe_user_id,
                agent_thread_id=agent_thread_id,
                settings=settings,
                thread_id=thread_id,
                user_message_id=user_message_id,
            )
            self._ensure_chat_assistant_target(
                session_id=existing["session_id"],
                backend=normalized_backend,
                safe_user_id=safe_user_id,
                settings=settings,
            )
            logging.getLogger(__name__).info(
                "metric_chat_session_mapping_reused backend=%s assistant_scope=%s mapping_user_id=%s session_id=%s",
                normalized_backend,
                scope,
                mapping_user_id,
                existing.get("session_id"),
            )
            return {
                "session_id": existing["session_id"],
                "agent_thread_id": agent_thread_id,
                "vm_name": _chat_assistant_vm_name(
                    backend=normalized_backend,
                    session_id=existing["session_id"],
                ),
                "execution_backend": normalized_backend,
                "capability_profile": _assistant_capability_profile(settings),
            }

        if normalized_backend == "libvirt_vm":
            session_id = "assistant-shared"
        else:
            session_id = f"assistant-{safe_user_id}"
        agent_thread_id = ""
        persisted = self._assistant_session_repo.upsert_mapping(
            user_id=mapping_user_id,
            backend=normalized_backend,
            assistant_scope=scope,
            session_id=session_id,
            agent_thread_id=agent_thread_id,
            thread_id=thread_id,
        )
        self._ensure_chat_assistant_manifest(
            session_id=session_id,
            backend=normalized_backend,
            user_id=normalized_user_id,
            safe_user_id=safe_user_id,
            agent_thread_id=agent_thread_id,
            settings=settings,
            thread_id=thread_id,
            user_message_id=user_message_id,
        )
        self._ensure_chat_assistant_target(
            session_id=session_id,
            backend=normalized_backend,
            safe_user_id=safe_user_id,
            settings=settings,
        )
        logging.getLogger(__name__).info(
            "metric_chat_session_mapping_created backend=%s assistant_scope=%s mapping_user_id=%s session_id=%s",
            normalized_backend,
            scope,
            mapping_user_id,
            session_id,
        )
        return {
            "session_id": persisted["session_id"],
            "agent_thread_id": agent_thread_id,
            "vm_name": _chat_assistant_vm_name(
                backend=normalized_backend,
                session_id=persisted["session_id"],
            ),
            "execution_backend": normalized_backend,
            "capability_profile": _assistant_capability_profile(settings),
        }

    def persist_chat_assistant_agent_thread_id(
        self,
        *,
        session_id: str,
        agent_thread_id: str,
        backend: str | None = None,
        assistant_scope: str | None = None,
        user_id: str | None = None,
        thread_id: str | None = None,
        queue_key: str | None = None,
    ) -> int:
        normalized_session_id = str(session_id or "").strip()
        normalized_thread_id = str(agent_thread_id or "").strip()
        if not normalized_session_id or not normalized_thread_id:
            return 0
        normalized_backend = str(backend or "").strip()
        normalized_scope = str(assistant_scope or "").strip() or "admin_chat"
        chat_thread_id = str(thread_id or "").strip() or _chat_thread_id_from_queue_key(queue_key)
        repository = self._session_repository()
        session = repository.get_session(normalized_session_id)
        metadata = dict(getattr(session, "metadata", {}) or {}) if session is not None else {}
        chat_threads = metadata.get("admin_chat_threads")
        if not isinstance(chat_threads, dict):
            chat_threads = {}
        normalized_user_id = str(user_id or "").strip()
        if not normalized_user_id and chat_thread_id:
            for raw_mapping in chat_threads.values():
                if not isinstance(raw_mapping, dict):
                    continue
                if str(raw_mapping.get("thread_id") or "").strip() != chat_thread_id:
                    continue
                normalized_user_id = str(raw_mapping.get("user_id") or "").strip()
                break

        updated = 0
        skip_repository_update = False
        if normalized_user_id:
            current_mapping = self._assistant_session_repo.get_mapping(
                user_id=normalized_user_id,
                backend=normalized_backend,
                assistant_scope=normalized_scope,
            )
            current_chat_thread_id = (
                str((current_mapping or {}).get("thread_id") or "").strip()
                if current_mapping is not None
                else ""
            )
            should_update_mapping = not (
                current_chat_thread_id
                and chat_thread_id
                and current_chat_thread_id != chat_thread_id
            )
            if not should_update_mapping:
                skip_repository_update = True
        if normalized_user_id and not skip_repository_update:
            self._assistant_session_repo.upsert_mapping(
                user_id=normalized_user_id,
                backend=normalized_backend,
                assistant_scope=normalized_scope,
                session_id=normalized_session_id,
                agent_thread_id=normalized_thread_id,
                thread_id=chat_thread_id,
            )
            updated += 1
        elif not skip_repository_update and not chat_thread_id:
            updated += self._assistant_session_repo.update_agent_thread_id_for_session(
                session_id=normalized_session_id,
                agent_thread_id=normalized_thread_id,
                backend=normalized_backend,
                assistant_scope=normalized_scope,
            )

        if session is not None and chat_thread_id:
            replacement: dict[str, Any] | None = None
            for raw_key, raw_mapping in list(chat_threads.items()):
                if not isinstance(raw_mapping, dict):
                    continue
                if str(raw_mapping.get("thread_id") or "").strip() != chat_thread_id:
                    continue
                replacement = dict(raw_mapping)
                if raw_key != normalized_thread_id:
                    chat_threads.pop(raw_key, None)
            if replacement is not None:
                replacement["thread_id"] = chat_thread_id
                if normalized_user_id:
                    replacement["user_id"] = normalized_user_id
                chat_threads[normalized_thread_id] = replacement
                metadata["admin_chat_threads"] = chat_threads
                repository.upsert_session(session.model_copy(update={"metadata": metadata}))
        return updated

    def reset_chat_assistant_thread_context(
        self,
        *,
        user_id: str,
        backend: str | None = None,
        assistant_scope: str | None = None,
        thread_id: str | None = None,
    ) -> dict[str, str]:
        normalized_backend = str(backend or "libvirt_vm").strip() or "libvirt_vm"
        normalized_user_id = str(user_id or "anonymous").strip() or "anonymous"
        normalized_scope = str(assistant_scope or "admin_chat").strip() or "admin_chat"
        normalized_chat_thread_id = str(thread_id or "").strip()
        existing = self._assistant_session_repo.get_mapping(
            user_id=normalized_user_id,
            backend=normalized_backend,
            assistant_scope=normalized_scope,
        )
        if existing is not None:
            session_id = existing["session_id"]
        elif normalized_backend == "libvirt_vm":
            session_id = "assistant-shared"
        else:
            session_id = f"assistant-{_safe_chat_assistant_identifier(normalized_user_id)}"

        self._assistant_session_repo.upsert_mapping(
            user_id=normalized_user_id,
            backend=normalized_backend,
            assistant_scope=normalized_scope,
            session_id=session_id,
            agent_thread_id="",
            thread_id=normalized_chat_thread_id,
        )

        repository = self._session_repository()
        session = repository.get_session(session_id)
        if session is not None:
            metadata = dict(getattr(session, "metadata", {}) or {})
            if "agent_thread_id" in metadata:
                metadata.pop("agent_thread_id", None)
                repository.upsert_session(session.model_copy(update={"metadata": metadata}))

        return {
            "session_id": session_id,
            "agent_thread_id": "",
            "thread_id": normalized_chat_thread_id,
        }

    def _stored_chat_assistant_thread_id(self, session_id: str) -> str:
        session = self._session_repository().get_session(session_id)
        metadata = getattr(session, "metadata", {}) if session is not None else {}
        if not isinstance(metadata, dict):
            return ""
        agent_thread_id = str(metadata.get("agent_thread_id") or "").strip()
        if _is_synthetic_chat_assistant_thread_id(agent_thread_id):
            return ""
        return agent_thread_id

    def _clear_stored_chat_assistant_thread_id(self, session_id: str) -> None:
        repository = self._session_repository()
        session = repository.get_session(session_id)
        if session is None:
            return
        metadata = dict(getattr(session, "metadata", {}) or {})
        if "agent_thread_id" not in metadata:
            return
        metadata.pop("agent_thread_id", None)
        repository.upsert_session(session.model_copy(update={"metadata": metadata}))

    def _ensure_chat_assistant_manifest(
        self,
        *,
        session_id: str,
        backend: str,
        user_id: str,
        safe_user_id: str,
        agent_thread_id: str,
        settings: dict[str, Any] | None,
        thread_id: str | None,
        user_message_id: str | None,
    ) -> None:
        repository = self._session_repository()
        existing = repository.get_session(session_id)
        profile = _assistant_capability_profile(settings)
        allowed_tools = _assistant_allowed_tools(profile)
        metadata = dict(getattr(existing, "metadata", {}) or {})
        metadata["capability_profile"] = profile
        metadata["assistant_scope"] = "admin_chat"
        metadata["execution_backend"] = backend
        metadata["assistant_read_only"] = bool((settings or {}).get("read_only", True))
        metadata["assistant_mutation_policy"] = _assistant_mutation_policy(
            profile=profile,
        )
        chat_threads = metadata.get("admin_chat_threads")
        if not isinstance(chat_threads, dict):
            chat_threads = {}
        if thread_id:
            chat_threads[
                _chat_thread_mapping_key(
                    agent_thread_id=agent_thread_id,
                    thread_id=thread_id,
                )
            ] = {
                "thread_id": thread_id,
                "user_id": user_id,
                "user_message_id": user_message_id or "",
            }
        metadata["admin_chat_threads"] = chat_threads
        labels = metadata.get("labels")
        if not isinstance(labels, dict):
            labels = {}
        labels.update({"system": "chat_assistant", "user": safe_user_id})
        metadata["labels"] = labels

        vm_name = _chat_assistant_vm_name(backend=backend, session_id=session_id)
        vm_provider = "libvirt" if backend == "libvirt_vm" else backend
        vm_provider_id = vm_name
        expires_at = datetime.now(UTC) + timedelta(days=30)
        if existing is not None:
            expires_at = max(getattr(existing, "expires_at", expires_at), expires_at)
            manifest = existing.model_copy(
                update={
                    "vm_id": vm_name if backend == "libvirt_vm" else None,
                    "vm_provider": vm_provider,
                    "vm_provider_id": vm_provider_id,
                    "model": str((settings or {}).get("default_model") or "").strip() or None,
                    "allowed_tools": allowed_tools,
                    "metadata": metadata,
                    "expires_at": expires_at,
                }
            )
            repository.upsert_session(manifest)
            return

        manifest = SessionManifest(
            session_id=session_id,
            vm_id=vm_name if backend == "libvirt_vm" else None,
            vm_provider=vm_provider,
            vm_provider_id=vm_provider_id,
            model=str((settings or {}).get("default_model") or "").strip() or None,
            directory_path="root",
            allowed_tools=allowed_tools,
            expires_at=expires_at,
            metadata=metadata,
        )
        repository.upsert_session(manifest)

    def _ensure_chat_assistant_target(
        self,
        *,
        session_id: str,
        backend: str,
        safe_user_id: str,
        settings: dict[str, Any] | None,
    ) -> None:
        if backend == ExecutionBackendKind.LIBVIRT_VM.value:
            self._ensure_libvirt_chat_assistant_target(
                session_id=session_id,
                safe_user_id=safe_user_id,
                settings=settings,
            )
            return
        if backend == ExecutionBackendKind.CONTAINER_WORKER_VM.value:
            self._ensure_container_chat_assistant_target(
                session_id=session_id,
                safe_user_id=safe_user_id,
                settings=settings,
            )

    def _ensure_libvirt_chat_assistant_target(
        self,
        *,
        session_id: str,
        safe_user_id: str,
        settings: dict[str, Any] | None,
    ) -> None:
        vm_name = _chat_assistant_vm_name(
            backend=ExecutionBackendKind.LIBVIRT_VM.value,
            session_id=session_id,
        )
        if vm_helpers.dominfo_field(vm_name, "State") is None:
            result = self.create_vm(
                provider="libvirt",
                vm_name=vm_name,
                session_id=session_id,
                labels={"system": "chat_assistant", "user": safe_user_id},
                model=str((settings or {}).get("default_model") or "").strip() or None,
                vm_user="agent",
                ttl_hours=24 * 30,
                allowed_tools=[
                    str(tool.value)
                    for tool in _assistant_allowed_tools(_assistant_capability_profile(settings))
                ],
                directory_path="root",
                allow_existing_session=True,
            )
            if not result.success and vm_helpers.dominfo_field(vm_name, "State") is None:
                raise RuntimeError(result.message or f"Failed to create assistant VM {vm_name}")
        self.vm_copy_files(
            session_id,
            files=_assistant_home_file_specs(self._deps.project_dir),
            vm_user="agent",
        )
        self._ensure_libvirt_chat_assistant_runtime(
            session_id=session_id,
            vm_name=vm_name,
            settings=settings,
        )

    def _ensure_libvirt_chat_assistant_runtime(
        self,
        *,
        session_id: str,
        vm_name: str,
        settings: dict[str, Any] | None,
    ) -> None:
        try:
            check = self.vm_exec(
                session_id,
                command=[
                    "bash",
                    "-lc",
                    'test -x "$HOME/bin/codex" || command -v codex >/dev/null 2>&1',
                ],
                vm_user="agent",
                timeout_seconds=20.0,
                check=False,
            )
            if int(check.get("exit_code", 1)) == 0:
                return
        except Exception:
            pass

        root_dir = _source_project_dir()
        bootstrap_script = root_dir / "scripts" / "bootstrap_codex_vm.sh"
        run_env = os.environ.copy()
        default_model = str((settings or {}).get("default_model") or "").strip()
        if default_model:
            run_env["AGENT_MODEL"] = default_model
        completed = process_helpers.run_subprocess(
            [str(bootstrap_script), vm_name, "agent"],
            capture_output=True,
            text=True,
            env=run_env,
        )
        if completed.returncode != 0:
            message = process_helpers.combined_process_message(completed.stdout, completed.stderr)
            raise RuntimeError(
                message or f"{bootstrap_script.name} exited with code {completed.returncode}"
            )

    def _ensure_container_chat_assistant_target(
        self,
        *,
        session_id: str,
        safe_user_id: str,
        settings: dict[str, Any] | None,
    ) -> None:
        backend = self.container_worker_backend(directory_path="root")
        backend.prepare_session(
            session_id=session_id,
            metadata={
                "execution_backend": ExecutionBackendKind.CONTAINER_WORKER_VM.value,
                "directory_path": "root",
                "target_name": _chat_assistant_vm_name(
                    backend=ExecutionBackendKind.CONTAINER_WORKER_VM.value,
                    session_id=session_id,
                ),
                "labels": {"system": "chat_assistant", "user": safe_user_id},
            },
        )
        from evander.effects.providers.agent_session import build_container_session_files

        backend.write_files(
            session_id=session_id,
            files=[
                *build_container_session_files(
                    config=backend.config,
                    session_id=session_id,
                    model=str((settings or {}).get("default_model") or "").strip() or None,
                    dotnet=False,
                    nuget=False,
                    playwright=False,
                ),
                *_assistant_home_file_specs(self._deps.project_dir),
            ],
        )
