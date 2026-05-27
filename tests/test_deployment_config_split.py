from pathlib import Path
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[1]


def _tracked_repo_paths(*paths: str) -> set[str]:
    result = subprocess.run(
        ["git", "ls-files", "--", *paths],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return set(result.stdout.splitlines())


def test_client_deployment_files_are_not_product_repo_sources() -> None:
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
    docs = (ROOT / "ansible" / "README.md").read_text(encoding="utf-8")
    local_only_paths = {
        "azure-pipelines.yml",
        "ansible/inventory/azure.yml",
        "ansible/group_vars/azure.yml",
        "ansible/group_vars/all/vault.yml",
    }

    assert _tracked_repo_paths(*sorted(local_only_paths)).isdisjoint(local_only_paths)

    assert "/azure-pipelines.yml" in ignored
    assert "ansible/inventory/azure.yml" in ignored
    assert "ansible/group_vars/azure.yml" in ignored
    assert "ansible/group_vars/all/vault.yml" in ignored

    assert (
        "Azure Pipelines deployment YAML is intentionally not checked into this product repo"
        in docs
    )
    assert "operator-owned deployment config" in docs


def test_azure_deployment_examples_are_non_secret_templates() -> None:
    inventory_path = ROOT / "ansible" / "inventory" / "azure.example.yml"
    vars_path = ROOT / "ansible" / "group_vars" / "azure.example.yml"
    vault_path = ROOT / "ansible" / "group_vars" / "all" / "vault.yml.example"

    inventory_text = inventory_path.read_text(encoding="utf-8")
    vars_text = vars_path.read_text(encoding="utf-8")
    vault_text = vault_path.read_text(encoding="utf-8")

    inventory = yaml.safe_load(inventory_text)
    vars_payload = yaml.safe_load(vars_text)
    vault_payload = yaml.safe_load(vault_text)

    assert inventory["all"]["vars"]["agent_sandbox_control_plane_public_host"] == "203.0.113.10"
    assert "AGENT_SANDBOX_CONTROL_PLANE_SSH_KEY_FILE" in inventory_text
    assert "AGENT_SANDBOX_WORKER_SSH_KEY_FILE" in inventory_text
    assert "ProxyCommand" in inventory_text
    assert "StrictHostKeyChecking=accept-new" in inventory_text
    assert "IdentitiesOnly=yes" in inventory_text
    assert 'agent_sandbox_worker_docker_network: "bridge"' in inventory_text
    assert "agent_sandbox_worker_egress_policy_enabled: true" in inventory_text

    assert vars_payload["agent_sandbox_repo_url"] == "git@example.com:your-org/agent-sandbox.git"
    assert vars_payload["agent_sandbox_install_azure_cli"] is True
    assert vars_payload["agent_sandbox_admin_web_caddy_enabled"] is True
    assert vars_payload["agent_sandbox_nexus_admin_password"] == (
        "{{ vault_agent_sandbox_nexus_admin_password | default('') }}"
    )
    assert (
        vars_payload["agent_sandbox_admin_web_env"]["AGENT_SANDBOX_ADMIN_WEB_PUBLIC_BASE_URL"]
        == "https://admin.example.com"
    )

    assert set(vault_payload) >= {
        "vault_openai_api_key",
        "vault_admin_web_password",
        "vault_agent_sandbox_repo_ssh_private_key",
        "vault_agent_sandbox_worker_token",
        "vault_agent_sandbox_nexus_admin_password",
    }
    assert all(value == "" for value in vault_payload.values())

    forbidden = (
        "truman",
        "52.158.33.185",
        "northeurope.cloudapp.azure.com",
        "jonathan.quinth",
        "sk-proj-",
        "azdpat",
    )
    combined = "\n".join([inventory_text, vars_text, vault_text])
    for marker in forbidden:
        assert marker not in combined
