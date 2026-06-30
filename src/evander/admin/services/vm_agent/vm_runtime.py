from __future__ import annotations

from evander.admin.services.vm_agent.common import (
    Any,
    Path,
    SessionManifest,
    VmActionResult,
    VmProviderRegistry,
    VmSummary,
    _VmProviderRegistryDependencies,
    _source_project_dir,
    os,
    process_helpers,
    session_helpers,
    subprocess,
    vm_helpers,
)


class VmAgentRuntimeMixin:
    def _resolve_session_target(self, target: str):
        repository = self._session_repository()
        try:
            session = repository.resolve_session_target(target)
        except Exception:
            session = None
        if session is not None:
            return str(getattr(session, "session_id", "") or "").strip(), session
        sessions = repository.read_all()
        return session_helpers.resolve_session_target(target, sessions)

    def _persist_session(self, session_id: str, manifest: Any) -> None:
        repository = self._session_repository()
        manifest_session_id = str(getattr(manifest, "session_id", "") or "").strip()
        target_session_id = manifest_session_id or str(session_id or "").strip()
        get_session = getattr(repository, "get_session", None)
        previous_session = (
            get_session(target_session_id) if callable(get_session) and target_session_id else None
        )
        if manifest_session_id:
            repository.upsert_session(manifest)
        else:
            put = getattr(repository, "put", None)
            if callable(put):
                put(session_id, manifest)
            else:
                repository.upsert_session(manifest)
        if isinstance(manifest, SessionManifest):
            session_helpers.sync_added_discord_session_cursors(
                self._deps.runtime,
                previous_session=previous_session,
                updated_session=manifest,
            )

    @staticmethod
    def _control_ssh_users(vm_user: str) -> list[str]:
        configured = (
            os.environ.get("EVANDER_VM_ADMIN_USER", "agent-admin").strip() or "agent-admin"
        )
        users = [configured]
        if vm_user and vm_user not in users:
            users.append(vm_user)
        return users

    def _resolve_control_ssh_access(self, vm_name: str, *, vm_user: str) -> tuple[str, str, Path]:
        errors: list[str] = []
        for ssh_user in self._control_ssh_users(vm_user):
            try:
                vm_ip, ssh_private_key_path = vm_helpers.resolve_vm_ssh_access(vm_name, ssh_user)
                return ssh_user, vm_ip, ssh_private_key_path
            except RuntimeError as exc:
                errors.append(str(exc))
        detail = errors[-1] if errors else f"Unable to resolve SSH access for {vm_name}"
        raise RuntimeError(detail)

    def _provider_registry(self) -> VmProviderRegistry:
        dependency_view = _VmProviderRegistryDependencies(
            runtime=self._deps.runtime,
            directory_path=self._deps.directory_path,
            resolve_vm_defaults=self._deps.resolve_vm_defaults,
            resolve_agent_model_for_session=self._deps.resolve_agent_model_for_session,
            privileged_helper=self._deps.privileged_helper,
            directory_ownership=self._deps.directory_ownership,
            agent_control_store=self._deps.agent_control_store,
        )
        return VmProviderRegistry(dependency_view)

    def _find_session_for_vm(self, vm_name: str) -> tuple[str | None, Any | None]:
        matches: list[tuple[str, Any]] = []
        for session in self._session_repository().list_sessions_for_vm(vm_name):
            session_id = str(getattr(session, "session_id", "") or "").strip()
            if str(getattr(session, "vm_id", "") or "").strip() == vm_name:
                matches.append((session_id, session))
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            session_ids = ", ".join(sorted(session_id for session_id, _ in matches))
            raise ValueError(f"VM {vm_name!r} is associated with multiple sessions: {session_ids}")
        return None, None

    def _resolve_provider_operation_target(self, target: str) -> tuple[Any, str, Any | None, str]:
        repository = self._session_repository()
        direct_session = repository.get_session(target)
        provider, vm_name, session = self._provider_registry().resolve_target(
            target=target,
            session_repository=repository,
            directory_path=str(getattr(direct_session, "directory_path", "") or "root"),
        )
        directory_path = str(getattr(session, "directory_path", "") or "root")
        return provider, vm_name, session, directory_path

    def list_vms(
        self,
        *,
        include_inactive: bool = True,
        omit_state: bool = False,
        vm_user: str = "agent",
        directory_path: str | None = None,
        direct_only: bool = False,
    ) -> list[VmSummary]:
        try:
            summaries = self._provider_registry().list_vms(
                include_inactive=include_inactive,
                omit_state=omit_state,
                vm_user=vm_user,
                directory_path=directory_path,
                direct_only=direct_only,
            )
            if summaries:
                return summaries
        except RuntimeError:
            pass
        return self._deps.vm_inventory_admin.list_vms(
            include_inactive=include_inactive,
            omit_state=omit_state,
            vm_user=vm_user,
            directory_path=directory_path,
            direct_only=direct_only,
        )

    def start_vm(self, target: str) -> VmActionResult:
        provider, vm_name, session, directory_path = self._resolve_provider_operation_target(target)
        return provider.start_vm(
            target=target, vm_name=vm_name, session=session, directory_path=directory_path
        )

    def shutdown_vm(self, target: str, *, timeout_seconds: int = 60) -> VmActionResult:
        provider, vm_name, session, directory_path = self._resolve_provider_operation_target(target)
        return provider.shutdown_vm(
            target=target,
            vm_name=vm_name,
            session=session,
            directory_path=directory_path,
            timeout_seconds=timeout_seconds,
        )

    def _run_script(
        self, script_name: str, args: list[str], *, env: dict[str, str] | None = None
    ) -> VmActionResult:
        script_path = _source_project_dir() / "scripts" / script_name
        completed = process_helpers.run_subprocess(
            [str(script_path), *args], capture_output=True, text=True, env=env
        )
        vm_name = ""
        if "--vm-name" in args:
            try:
                vm_name = args[args.index("--vm-name") + 1]
            except IndexError:
                vm_name = ""
        elif len(args) > 1:
            issue_target = args[1]
            issue_number = session_helpers.extract_issue_number(issue_target) or issue_target
            vm_name = session_helpers.resolve_vm_name(issue_number) if issue_number else ""
        message = (completed.stdout or completed.stderr or "").strip()
        return VmActionResult(
            success=completed.returncode == 0,
            vm_name=vm_name,
            action=script_name.removesuffix(".sh"),
            message=message or f"{script_name} exited with code {completed.returncode}",
        )

    def _run_privileged_host_script(
        self,
        script_path: Path,
        args: list[str],
        *,
        env: dict[str, str] | None = None,
        allowed_env_keys: tuple[str, ...] = (),
    ) -> tuple[subprocess.CompletedProcess[str], str]:
        run_env = env or os.environ.copy()
        base_cmd = [str(script_path), *args]
        completed = process_helpers.run_subprocess(
            process_helpers.sudo_env_command(base_cmd, run_env, allowed_env_keys),
            capture_output=True,
            text=True,
            env=run_env,
        )
        message = process_helpers.combined_process_message(completed.stdout, completed.stderr)
        if completed.returncode != 0 and process_helpers.can_prompt_for_privileges():
            completed = process_helpers.run_subprocess(
                process_helpers.pkexec_env_command(base_cmd, run_env, allowed_env_keys),
                capture_output=True,
                text=True,
                env=run_env,
            )
            message = process_helpers.combined_process_message(completed.stdout, completed.stderr)
        return completed, message

    def enable_agent(self, target: str, *, vm_user: str = "agent") -> VmActionResult:
        vm_name = self._resolve_vm_name_from_target(target)
        return self._run_script(
            "set_issue_agent_state.sh",
            [
                "--state",
                "enable",
                "--vm-name",
                vm_name,
                "--vm-user",
                vm_user,
                "--host-state-path",
                str(self._deps.config.host_agent_control_state_path),
            ],
        )

    def disable_agent(self, target: str, *, vm_user: str = "agent") -> VmActionResult:
        vm_name = self._resolve_vm_name_from_target(target)
        return self._run_script(
            "set_issue_agent_state.sh",
            [
                "--state",
                "disable",
                "--vm-name",
                vm_name,
                "--vm-user",
                vm_user,
                "--host-state-path",
                str(self._deps.config.host_agent_control_state_path),
            ],
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
        provider, vm_name, session, directory_path = self._resolve_provider_operation_target(target)
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
        provider, vm_name, session, directory_path = self._resolve_provider_operation_target(target)
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
        provider, vm_name, session, directory_path = self._resolve_provider_operation_target(target)
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
            return (
                "",
                None,
                "root",
                VmActionResult(success=False, vm_name=vm_name, action=action, message=str(exc)),
            )

        resolved_session_id = (
            session_id.strip() if session_id and session_id.strip() else inferred_session_id
        )
        session = inferred_session
        if resolved_session_id:
            session = self._session_repository().get_session(resolved_session_id)
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
            resolved_session_id = session_helpers.default_vm_session_id(vm_name)

        if session is None and vm_helpers.dominfo_field(vm_name, "State") is None:
            return (
                "",
                None,
                "root",
                VmActionResult(
                    success=False,
                    vm_name=vm_name,
                    action=action,
                    message=f"VM not found: {vm_name}",
                ),
            )

        normalized_directory_path = str(
            getattr(session, "directory_path", "") or ""
        ).strip() or self._deps.directory_path.normalize_directory_path(directory_path)
        return resolved_session_id, session, normalized_directory_path, None

    resolve_session_target = _resolve_session_target
    persist_session = _persist_session
    control_ssh_users = _control_ssh_users
    resolve_control_ssh_access = _resolve_control_ssh_access
    provider_registry = _provider_registry
    find_session_for_vm = _find_session_for_vm
    resolve_provider_operation_target = _resolve_provider_operation_target
    run_script = _run_script
    run_privileged_host_script = _run_privileged_host_script
    resolve_vm_operation_context = _resolve_vm_operation_context
