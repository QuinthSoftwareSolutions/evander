from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_cloud_init_contains_provider_neutral_session_and_profile_exports() -> None:
    template = (ROOT / "config" / "cloud-init-user-data.yaml").read_text(encoding="utf-8")

    assert "/etc/agent-sandbox/session.env" in template
    assert "/home/agent/.agent/config.env" in template
    assert "AGENT_SANDBOX_PROVIDER_CONFIG_VERSION=1" in template
    assert "HTTP_TARGET_RELAY_BASE_URL=__HTTP_TARGET_RELAY_BASE_URL__" in template
    assert "AGENT_SANDBOX_LLM_BASE_URL=__LLM_BASE_URL__" in template
    assert "/home/agent/.codex/config.toml" not in template
    assert 'OPENAI_API_KEY="${OPENAI_API_KEY:-relay-placeholder}"' in template
    assert "  - git" in template
    assert "  - ripgrep" in template
    assert "  - name: agent-admin" in template
    assert "  - install -d -m 0700 -o agent -g agent /home/agent/.agent-sandbox" in template
    assert "  - install -d -m 0700 -o root -g root /var/log/agent-sandbox/agent-runs" in template
