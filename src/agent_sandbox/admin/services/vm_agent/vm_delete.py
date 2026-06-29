from __future__ import annotations

from agent_sandbox.admin.services.vm_agent.common import (
    VmActionResult,
    _source_project_dir,
    git_helpers,
    session_helpers,
)


class VmAgentDeleteMixin:
    def destroy_vm(self, target: str) -> VmActionResult:
        vm_name = target.strip()
        if not vm_name:
            return VmActionResult(
                success=False, vm_name="", action="destroy_vm", message="VM name is required."
            )
        issue_number = session_helpers.extract_issue_number(vm_name)
        if issue_number is not None and vm_name == f"vm-{issue_number}":
            return self.destroy_issue_vm(issue_number)

        root_dir = _source_project_dir()
        destroy_script = root_dir / "scripts" / "destroy_agent_vm.sh"
        helper = (
            self._deps.privileged_helper.get_privileged_helper()
            if self._deps.privileged_helper.get_use_privileged_helper()
            else None
        )
        if helper is not None and not helper.is_running():
            helper.start()
        if helper is not None:
            response = helper.call("destroy_vm", script_path=str(destroy_script), vm_name=vm_name)
            message = str(response.get("stdout") or response.get("stderr") or "").strip()
        else:
            completed, message = self._run_privileged_host_script(destroy_script, [vm_name])
            if completed.returncode != 0:
                return VmActionResult(
                    success=False,
                    vm_name=vm_name,
                    action="destroy_vm",
                    message=message or f"destroy-vm exited with code {completed.returncode}",
                )

        session_repository = self._session_repository()
        session_ids = {
            session.session_id for session in session_repository.list_sessions_for_vm(vm_name)
        }
        for session_id in session_ids:
            session_repository.delete_session(session_id)
        removed_approvals = session_helpers.prune_approvals_for_sessions(
            self._deps.runtime, session_ids=session_ids
        )
        removed_pr_map_entries, removed_streams = git_helpers.prune_pr_map(
            self._deps.config.pr_map_path, session_ids=session_ids
        )
        removed_cursors, removed_feedback = git_helpers.prune_pr_feedback_store(
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
        issue_number = session_helpers.extract_issue_number(target) or target
        vm_name = f"vm-{issue_number}" if issue_number else target
        root_dir = _source_project_dir()
        destroy_script = root_dir / "scripts" / "destroy_agent_vm.sh"

        completed, message = self._run_privileged_host_script(destroy_script, [vm_name])
        if completed.returncode != 0:
            return VmActionResult(
                success=False,
                vm_name=vm_name,
                action="destroy_issue_vm",
                message=message or f"destroy-issue-vm exited with code {completed.returncode}",
            )

        session_repository = self._session_repository()
        session_ids = {
            session.session_id
            for session in session_repository.list_sessions_for_issue(issue_number)
        }
        for session_id in session_ids:
            session_repository.delete_session(session_id)
        removed_approvals = session_helpers.prune_approvals_for_sessions(
            self._deps.runtime, session_ids=session_ids
        )
        removed_pr_map_entries, removed_streams = git_helpers.prune_pr_map(
            self._deps.config.pr_map_path, session_ids=session_ids
        )
        removed_cursors, removed_feedback = git_helpers.prune_pr_feedback_store(
            self._deps.config.runtime_dir / "pr-feedback-store.json",
            streams=removed_streams,
        )
        bare_repo_removed, bare_repo_path = git_helpers.remove_issue_bare_repo(
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
