from __future__ import annotations

from evander.admin.services.vm_agent.common import (
    MODEL_PROVIDER_MODE_METADATA_KEY,
    Any,
    Path,
    SimpleNamespace,
    VmActionResult,
    _managed_codex_skills_root,
    _source_project_dir,
    normalize_model_provider_mode_override,
    os,
    process_helpers,
    session_helpers,
    vm_helpers,
)
from evander.fast_mode import FastModeValue, normalize_fast_mode, validate_session_fast_mode
from evander.provider_config import validate_openrouter_session
from evander.reasoning_effort import normalize_reasoning_effort, validate_session_reasoning_effort
from evander.repositories.skills_library import record_codex_skill_installations


class VmAgentIssueCreateMixin:
    def create_issue_vm(
        self,
        *,
        issue: str,
        session_id: str | None = None,
        model: str | None = None,
        cost_category: str | None = None,
        reasoning_effort: str | None = None,
        fast_mode: FastModeValue | None = None,
        model_provider_mode: str | None = None,
        vm_user: str = "agent",
        ttl_hours: int | None = None,
        allowed_contact_ids: list[str] | None = None,
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
        issue_number = session_helpers.resolve_issue_number(issue_input)
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
        resolved_session_id = session_id or session_helpers.default_issue_session_id(issue_number)
        resolved_vm_name = f"vm-{issue_number}"
        normalized_directory_path = self._deps.directory_path.normalize_directory_path(
            local_issue.directory_path
            if local_issue is not None and directory_path == "root"
            else directory_path
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
        resolved_allowed_contact_ids = (
            session_helpers.validate_allowed_contact_ids_for_runtime_directory(
                self._deps.runtime,
                session_id=resolved_session_id,
                directory_path=normalized_directory_path,
                allowed_contact_ids=allowed_contact_ids,
            )
        )
        normalized_codex_skills = [
            item.strip() for item in (codex_skills or []) if item and item.strip()
        ]

        manifest = session_helpers.build_issue_session_manifest(
            policy=policy,
            session_id=resolved_session_id,
            vm_id=resolved_vm_name,
            issue_number=issue_number,
            directory_path=normalized_directory_path,
            ttl_hours=ttl_hours,
            allowed_contact_ids=resolved_allowed_contact_ids,
            discord_channels=discord_channels or [],
            email_mailboxes=email_mailboxes or [],
            email_folders=email_folders or [],
        )
        resolved_model_provider_mode = normalize_model_provider_mode_override(model_provider_mode)
        if resolved_model_provider_mode is not None:
            manifest.metadata = dict(manifest.metadata)
            manifest.metadata[MODEL_PROVIDER_MODE_METADATA_KEY] = resolved_model_provider_mode
        if normalized_codex_skills:
            manifest.metadata = record_codex_skill_installations(
                manifest.metadata,
                normalized_codex_skills,
                backend="libvirt_vm",
                vm_user=vm_user,
            )
        normalized_model = (model or "").strip() or None
        if normalized_model is not None:
            manifest.model = normalized_model
        else:
            try:
                manifest.model = self._deps.resolve_agent_model_for_session(manifest)
            except ValueError:
                manifest.model = None
        from evander.cost_categories import normalize_cost_category

        manifest.cost_category = normalize_cost_category(cost_category)
        if reasoning_effort is not None:
            manifest.reasoning_effort = normalize_reasoning_effort(reasoning_effort)
        if fast_mode is not None:
            manifest.fast_mode = normalize_fast_mode(fast_mode)
        validate_session_reasoning_effort(
            manifest,
            self._deps.runtime.provider_config.resolve_for_directory(manifest.directory_path),
        )
        validate_session_fast_mode(
            manifest,
            self._deps.runtime.provider_config.resolve_for_directory(manifest.directory_path),
        )
        validate_openrouter_session(
            self._deps.runtime.provider_config.resolve_for_directory(normalized_directory_path),
            manifest,
        )
        self._persist_session(resolved_session_id, manifest)

        def _issue_vm_failure(message: str) -> VmActionResult:
            try:
                preserved_vm = vm_helpers.dominfo_field(resolved_vm_name, "State") is not None
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
            root_dir = _source_project_dir()
            setup_script = root_dir / "scripts" / "setup_vm.sh"
            vm_defaults = self._deps.resolve_vm_defaults(normalized_directory_path)
            issue_setup = self._deps.resolve_issue_setup(
                normalized_directory_path, root_dir=root_dir
            )
            run_env = os.environ.copy()
            run_env.update(vm_defaults.guest_environment())
            if manifest.model:
                run_env["AGENT_MODEL"] = manifest.model
            managed_skills_root = _managed_codex_skills_root(
                self._deps.config.runtime_dir,
                normalized_codex_skills,
            )
            if managed_skills_root is not None:
                run_env["HOST_REPO_CODEX_SKILLS_DIR"] = str(managed_skills_root)
            run_env.update(
                {
                    "EVANDER_GIT_HOST": issue_setup.git_host,
                    "EVANDER_GIT_USER": issue_setup.git_user,
                    "EVANDER_GIT_SSH_ALIAS": issue_setup.git_ssh_alias,
                    "EVANDER_AZURE_DEVOPS_PR_PAT_ENV": issue_setup.issue_setup_azure_devops_pr_pat_env,
                }
            )
            if issue_setup.post_exec_verification_prompt is not None:
                run_env["AGENT_POST_EXEC_VERIFICATION_PROMPT"] = (
                    issue_setup.post_exec_verification_prompt
                )

            helper = (
                self._deps.privileged_helper.get_privileged_helper()
                if self._deps.privileged_helper.get_use_privileged_helper()
                else None
            )
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
            for skill_name in normalized_codex_skills:
                setup_args.extend(["--codex-skill", skill_name])
            setup_args.extend([resolved_vm_name, resolved_session_id, vm_user])
            if helper is not None:
                response = helper.call(
                    "run_script",
                    script_path=str(setup_script),
                    args=setup_args,
                    env=run_env,
                )
                message = process_helpers.combined_process_message(
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
                    "AGENT_PROVIDER",
                    "HOST_CLAUDE_BIN",
                    "NUGET_RELAY_BASE_URL",
                    "EVANDER_RUNTIME_DIR",
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
                    "EVANDER_GIT_HOST",
                    "EVANDER_GIT_USER",
                    "EVANDER_GIT_SSH_ALIAS",
                    "EVANDER_AZURE_DEVOPS_PR_PAT_ENV",
                    "AGENT_POST_EXEC_VERIFICATION_PROMPT",
                    "HOST_REPO_CODEX_SKILLS_DIR",
                )
                launch_step = process_helpers.sudo_env_command(
                    [str(setup_script), *setup_args],
                    run_env,
                    allowed_env_keys,
                )
                completed = process_helpers.run_subprocess(
                    launch_step, capture_output=True, text=True, env=run_env
                )
                message = process_helpers.combined_process_message(
                    completed.stdout, completed.stderr
                )
                if completed.returncode != 0 and process_helpers.can_prompt_for_privileges():
                    launch_step = process_helpers.pkexec_env_command(
                        [str(setup_script), *setup_args],
                        run_env,
                        allowed_env_keys,
                    )
                    completed = process_helpers.run_subprocess(
                        launch_step, capture_output=True, text=True, env=run_env
                    )
                    message = process_helpers.combined_process_message(
                        completed.stdout, completed.stderr
                    )
        except Exception as exc:
            return _issue_vm_failure(str(exc) or f"Failed to create VM {resolved_vm_name}")

        if completed.returncode != 0:
            return _issue_vm_failure(
                message or f"{setup_script.name} exited with code {completed.returncode}"
            )
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
        cost_category: str | None = None,
        reasoning_effort: str | None = None,
        fast_mode: FastModeValue | None = None,
        model_provider_mode: str | None = None,
        vm_user: str = "agent",
        dotnet: bool = False,
        nuget: str | None = None,
        mssql: bool = False,
        mssql_init_root: str | None = None,
        playwright: bool = False,
        codex_skills: list[str] | None = None,
    ) -> VmActionResult:
        try:
            issue = self._deps.resolve_local_issue_record(issue_identifier)
        except ValueError as exc:
            return VmActionResult(
                success=False,
                vm_name=f"vm-{issue_identifier}",
                action="create_issue_vm",
                message=str(exc),
            )
        return self.create_issue_vm(
            issue=issue.issue_id,
            model=model,
            cost_category=cost_category,
            reasoning_effort=reasoning_effort,
            fast_mode=fast_mode,
            model_provider_mode=model_provider_mode,
            vm_user=vm_user,
            dotnet=dotnet,
            nuget=nuget,
            mssql=mssql,
            mssql_init_root=mssql_init_root,
            playwright=playwright,
            codex_skills=codex_skills,
            directory_path=issue.directory_path,
        )
