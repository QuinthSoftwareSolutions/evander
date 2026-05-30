from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_cloud_init_contains_provider_neutral_session_and_profile_exports() -> None:
    template = (ROOT / "config" / "cloud-init-user-data.yaml").read_text(encoding="utf-8")

    assert "manage_etc_hosts: true" in template
    assert "/etc/agent-sandbox/session.env" in template
    assert "/home/agent/.agent/config.env" in template
    assert "AGENT_SANDBOX_PROVIDER_CONFIG_VERSION=1" in template
    assert "HTTP_TARGET_RELAY_BASE_URL=__HTTP_TARGET_RELAY_BASE_URL__" in template
    assert "AGENT_SANDBOX_LLM_BASE_URL=__LLM_BASE_URL__" in template
    assert "AGENT_SANDBOX_ARTIFACTS_DIR=/artifacts" in template
    assert "/home/agent/.codex/config.toml" not in template
    assert "azure_openai_endpoint" not in template
    assert "azure_openai_api_key_env" not in template
    assert "azure_openai_model_deployments" not in template
    assert "https://example-resource.openai.azure.com" not in template
    assert "AZURE_OPENAI_API_KEY" not in template
    assert "deployment-gpt-5-4" not in template
    assert 'OPENAI_API_KEY="${OPENAI_API_KEY:-relay-placeholder}"' in template
    assert "  - git" in template
    assert "  - ripgrep" in template
    assert "  - name: agent-admin" in template
    assert "  - install -d -m 0700 -o agent -g agent /home/agent/.agent-sandbox" in template
    assert "  - install -d -m 0775 -o agent -g agent /artifacts" in template
    assert "  - ln -sfn /artifacts /home/agent/artifacts" in template
    assert "  - install -d -m 0700 -o root -g root /var/log/agent-sandbox/agent-runs" in template
