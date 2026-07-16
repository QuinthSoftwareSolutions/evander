from __future__ import annotations

from evander.admin.services.vm_agent.common import (
    VmActionResult,
    _revoke_managed_git_access_for_session,
    _source_project_dir,
    os,
    process_helpers,
    vm_helpers,
)


class VmAgentBootstrapMixin:
    def clone_repo_into_vm(
        self,
        *,
        vm_name: str,
        session_id: str | None = None,
        managed_repo_id: str,
        clone_branch: str | None = None,
        vm_user: str = "agent",
        directory_path: str = "root",
        git_author_name: str | None = None,
        git_author_email: str | None = None,
        git_committer_name: str | None = None,
        git_committer_email: str | None = None,
    ) -> VmActionResult:
        resolved_vm_name = vm_name.strip()
        if not resolved_vm_name:
            return VmActionResult(
                success=False,
                vm_name="",
                action="clone_repo_into_vm",
                message="VM name is required.",
            )

        normalized_managed_repo_id = managed_repo_id.strip()
        if not normalized_managed_repo_id:
            return VmActionResult(
                success=False,
                vm_name=resolved_vm_name,
                action="clone_repo_into_vm",
                message="managed_repo_id is required.",
            )
        normalized_clone_branch = clone_branch.strip() if clone_branch else ""

        resolved_session_id, session, normalized_directory_path, error_result = (
            self._resolve_vm_operation_context(
                vm_name=resolved_vm_name,
                session_id=session_id,
                action="clone_repo_into_vm",
                directory_path=directory_path,
            )
        )
        if error_result is not None:
            return error_result

        root_dir = _source_project_dir()
        issue_setup = self._deps.resolve_issue_setup(normalized_directory_path, root_dir=root_dir)
        vm_ip = vm_helpers.resolve_vm_ip(resolved_vm_name)

        managed_git = self._deps.managed_git_admin
        try:
            if managed_git is None:
                raise RuntimeError("managed Git service is not configured")
            clone_info = managed_git.clone_info(
                normalized_managed_repo_id,
                directory_path=normalized_directory_path,
                session_id=resolved_session_id,
            )
            if hasattr(clone_info, "model_dump"):
                clone_info = clone_info.model_dump(mode="python")
            if not isinstance(clone_info, dict):
                raise RuntimeError("managed Git clone lookup returned an invalid response")
            resolved_repo_name = str(
                clone_info.get("repo_name")
                or clone_info.get("display_name")
                or normalized_managed_repo_id
            ).strip()
            _bare_path, managed_git_root = managed_git.resolve_internal_repository(
                normalized_managed_repo_id,
                normalized_directory_path,
            )
            guest_repo_root = self._deps.clone_repo_into_vm(
                vm_name=resolved_vm_name,
                vm_user=vm_user,
                session_id=resolved_session_id,
                repo_name=resolved_repo_name,
                remote_repo_name=normalized_managed_repo_id,
                remote_git_root=str(managed_git_root),
                issue_setup=issue_setup,
                clone_branch=normalized_clone_branch or None,
                vm_ip=vm_ip,
                git_author_name=git_author_name,
                git_author_email=git_author_email,
                git_committer_name=git_committer_name,
                git_committer_email=git_committer_email,
            )
            refreshed_session = self._session_repository().get_session(resolved_session_id)
            access_metadata = (
                refreshed_session.metadata.get("managed_git_access")
                if refreshed_session is not None
                and isinstance(refreshed_session.metadata, dict)
                else None
            )
            managed_git.unbind_all("authorized_sessions", resolved_session_id)
            if (
                isinstance(access_metadata, dict)
                and access_metadata.get("managed_repo_id") == normalized_managed_repo_id
                and access_metadata.get("access_mode") == "write"
            ):
                managed_git.bind(
                    normalized_managed_repo_id,
                    kind="authorized_sessions",
                    binding_id=resolved_session_id,
                )
            managed_git.bind(
                normalized_managed_repo_id,
                kind="sessions",
                binding_id=resolved_session_id,
            )
        except Exception as exc:
            failed_session = self._session_repository().get_session(resolved_session_id)
            _revoke_managed_git_access_for_session(failed_session)
            if managed_git is not None:
                managed_git.unbind_all("authorized_sessions", resolved_session_id)
            if failed_session is not None and isinstance(failed_session.metadata, dict):
                cleaned_metadata = dict(failed_session.metadata)
                if cleaned_metadata.pop("managed_git_access", None) is not None:
                    self._session_repository().upsert_session(
                        failed_session.model_copy(update={"metadata": cleaned_metadata})
                    )
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
            remote_url=None,
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
            return VmActionResult(
                success=False,
                vm_name="",
                action="bootstrap_python_in_vm",
                message="VM name is required.",
            )

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

        python_script = _source_project_dir() / "scripts" / "bootstrap_python_vm.sh"
        try:
            completed = process_helpers.run_subprocess(
                [
                    str(python_script),
                    "--vm-user",
                    vm_user,
                    resolved_vm_name,
                    normalized_host_checkout,
                    normalized_target_dir,
                ],
                capture_output=True,
                text=True,
                env=os.environ
                | {"HOST_PYTHON_BIN": process_helpers.resolve_host_python_with_pip()},
            )
            message = process_helpers.combined_process_message(completed.stdout, completed.stderr)
            if completed.returncode != 0:
                raise RuntimeError(
                    message or f"{python_script.name} exited with code {completed.returncode}"
                )
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
            return VmActionResult(
                success=False,
                vm_name="",
                action="bootstrap_frontend_in_vm",
                message="VM name is required.",
            )

        normalized_host_checkout = host_checkout.strip()
        if not normalized_host_checkout:
            return VmActionResult(
                success=False,
                vm_name=resolved_vm_name,
                action="bootstrap_frontend_in_vm",
                message="host_checkout is required.",
            )

        normalized_install_roots = [
            item.strip() for item in (install_roots or []) if item and item.strip()
        ]
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

        frontend_script = _source_project_dir() / "scripts" / "bootstrap_frontend_deps_vm.sh"
        try:
            completed = process_helpers.run_subprocess(
                [
                    str(frontend_script),
                    "--vm-user",
                    vm_user,
                    resolved_vm_name,
                    normalized_host_checkout,
                    *normalized_install_roots,
                ],
                capture_output=True,
                text=True,
                env=os.environ.copy(),
            )
            message = process_helpers.combined_process_message(completed.stdout, completed.stderr)
            if completed.returncode != 0:
                raise RuntimeError(
                    message or f"{frontend_script.name} exited with code {completed.returncode}"
                )
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
