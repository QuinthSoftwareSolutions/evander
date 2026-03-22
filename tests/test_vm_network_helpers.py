from pathlib import Path


def test_issue_vm_scripts_source_shared_network_helper() -> None:
    setup = Path('/home/jonathan/agent-sandbox/scripts/setup_issue_vm.sh').read_text(encoding='utf-8')
    check = Path('/home/jonathan/agent-sandbox/scripts/check_issue_vm.sh').read_text(encoding='utf-8')

    assert 'source "${SCRIPT_DIR}/lib/vm_network.sh"' in setup
    assert 'source "${SCRIPT_DIR}/lib/vm_network.sh"' in check
    assert 'vm_network_wait_for_ip' in setup
    assert 'vm_network_wait_for_ssh' in setup
    assert 'vm_network_wait_for_ip' in check
    assert 'vm_network_wait_for_ssh' in check


def test_issue_vm_timeout_env_names_are_standardized() -> None:
    setup = Path('/home/jonathan/agent-sandbox/scripts/setup_issue_vm.sh').read_text(encoding='utf-8')
    check = Path('/home/jonathan/agent-sandbox/scripts/check_issue_vm.sh').read_text(encoding='utf-8')

    for text in (setup, check):
        assert 'VM_WAIT_IP_TIMEOUT_SECONDS' in text
        assert 'VM_WAIT_SSH_TIMEOUT_SECONDS' in text
        assert 'VM_IP_WAIT_TIMEOUT_SECONDS' not in text
        assert 'VM_SSH_WAIT_TIMEOUT_SECONDS' not in text


def test_timeout_env_names_are_documented_consistently() -> None:
    readme = Path('/home/jonathan/agent-sandbox/README.md').read_text(encoding='utf-8')
    operations = Path('/home/jonathan/agent-sandbox/docs/operations.md').read_text(encoding='utf-8')

    for text in (readme, operations):
        assert 'VM_WAIT_IP_TIMEOUT_SECONDS' in text
        assert 'VM_WAIT_SSH_TIMEOUT_SECONDS' in text
        assert 'default `180`' in text
