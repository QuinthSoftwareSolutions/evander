from pathlib import Path


SCRIPTS = [
    "apply_firewall.sh",
    "bootstrap_codex_vm.sh",
    "bootstrap_copilot_vm.sh",
    "bootstrap_frontend_deps_vm.sh",
    "bootstrap_dotnet_vm.sh",
    "bootstrap_git_vm.sh",
    "bootstrap_mssql_server_vm.sh",
    "bootstrap_mssql_tools_vm.sh",
    "bootstrap_mssql_vm.sh",
    "bootstrap_nuget_vm.sh",
    "bootstrap_playwright_vm.sh",
    "bootstrap_python_packages_vm.sh",
    "bootstrap_python_vm.sh",
    "check_issue_vm.sh",
    "create_base_vm.sh",
    "create_git_remote.sh",
    "create_libvirt_network.sh",
    "destroy_agent_vm.sh",
    "open_agent_pr.sh",
    "push_existing_pr_branch.sh",
    "promote_agent_branch.sh",
    "inspect_nuget_cache.sh",
    "install_systemd_units.sh",
    "launch_agent_vm.sh",
    "list_running_vms.sh",
    "check_session_vm_busy.sh",
    "git_ssh_command_wrapper.sh",
    "handle_agent_push.sh",
    "handle_git_push_trigger.sh",
    "mirror_issue_repo.sh",
    "prewarm_container_worker_nuget_cache.sh",
    "prewarm_nuget_cache.sh",
    "provision_exampleindustry_app_demo_environment.sh",
    "seed_workflow_run_inspector_demo.sh",
    "resume_issue_agent.sh",
    "resolve_repo_source.sh",
    "set_issue_agent_state.sh",
    "guest/push_agent.sh",
    "guest/sync_base.sh",
    "guest/setup_review_repo.sh",
    "setup_issue_git_vm.sh",
    "setup_vm.sh",
    "tail_issue_vm_log.sh",
]


def test_scripts_define_help_output() -> None:
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"

    for name in SCRIPTS:
        text = (scripts_dir / name).read_text(encoding="utf-8")
        assert "show_help()" in text
        assert '== "--help"' in text
        assert "Usage:" in text
