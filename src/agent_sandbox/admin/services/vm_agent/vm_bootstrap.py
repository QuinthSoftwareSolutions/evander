from __future__ import annotations

from agent_sandbox.admin.services.vm_agent.common import (
    VmActionResult,
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
        repo_source: str,
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

        normalized_repo_source = repo_source.strip()
        if not normalized_repo_source:
            return VmActionResult(
                success=False,
                vm_name=resolved_vm_name,
                action="clone_repo_into_vm",
                message="repo_source is required.",
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

        try:
            resolved_repo_path, resolved_repo_name = self._deps.resolve_repo_source(
                normalized_repo_source
            )
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
                git_author_name=git_author_name,
                git_author_email=git_author_email,
                git_committer_name=git_committer_name,
                git_committer_email=git_committer_email,
            )
            guest_remote_url = (
                f"ssh://{issue_setup.git_ssh_alias}{issue_setup.git_root}/{resolved_repo_name}.git"
            )
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
