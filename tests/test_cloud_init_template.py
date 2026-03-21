from pathlib import Path


def test_cloud_init_contains_codex_config_and_profile_exports() -> None:
    template = Path("/home/jonathan/agent-sandbox/config/cloud-init-user-data.yaml").read_text(
        encoding="utf-8"
    )

    assert "/etc/agent-sandbox/session.env" in template
    assert 'permissions: "0644"' in template
    assert "/home/agent/.codex/config.toml" in template
    assert 'openai_base_url = "__LLM_BASE_URL__"' in template
    assert 'OPENAI_API_KEY="${OPENAI_API_KEY:-relay-placeholder}"' in template
