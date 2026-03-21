from pathlib import Path


SCRIPTS = [
    "apply_firewall.sh",
    "bootstrap_codex_vm.sh",
    "bootstrap_dotnet_vm.sh",
    "bootstrap_git_vm.sh",
    "bootstrap_nuget_vm.sh",
    "bootstrap_playwright_vm.sh",
    "check_issue_vm.sh",
    "create_base_vm.sh",
    "create_git_remote.sh",
    "create_libvirt_network.sh",
    "destroy_agent_vm.sh",
    "open_agent_pr.sh",
    "promote_agent_branch.sh",
    "inspect_nuget_cache.sh",
    "install_systemd_units.sh",
    "launch_agent_vm.sh",
    "git_ssh_command_wrapper.sh",
    "handle_agent_push.sh",
    "mirror_issue_repo.sh",
    "prewarm_nuget_cache.sh",
    "setup_issue_git_vm.sh",
    "setup_issue_vm.sh",
    "tail_issue_vm_log.sh",
]


def test_scripts_define_help_output() -> None:
    scripts_dir = Path("/home/jonathan/agent-sandbox/scripts")

    for name in SCRIPTS:
        text = (scripts_dir / name).read_text(encoding="utf-8")
        assert "show_help()" in text
        assert '== "--help"' in text
        assert "Usage:" in text
