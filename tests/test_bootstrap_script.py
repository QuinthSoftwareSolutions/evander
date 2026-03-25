from pathlib import Path


def test_bootstrap_script_enforces_codex_config_and_placeholder_key() -> None:
    script = Path("/home/jonathan/agent-sandbox/scripts/bootstrap_codex_vm.sh").read_text(
        encoding="utf-8"
    )

    assert "sudo -n virsh domifaddr" in script
    assert 'printf "%s\\n" "${AGENT_SANDBOX_SESSION_ID:-}"' in script
    assert 'AGENT_SANDBOX_CONFIG="${AGENT_SANDBOX_CONFIG:-${ROOT_DIR}/config/gateway.yaml}"' in script
    assert 'cat > ~/.codex/config.toml <<CONFIG' in script
    assert 'model = "gpt-5.4"' in script
    assert 'model_reasoning_effort = "high"' in script
    assert 'personality = "pragmatic"' in script
    assert 'CODEX_LLM_BASE_URL="${LLM_BASE_URL:-${AGENT_SANDBOX_LLM_BASE_URL:-http://192.168.250.1:11434/v1}}"' in script
    assert 'CODEX_GATEWAY_URL="${AGENT_SANDBOX_GATEWAY_URL:-http://192.168.250.1:8000/mcp}"' in script
    assert 'openai_base_url = "${CODEX_LLM_BASE_URL}"' in script
    assert "[mcp_servers.agent_sandbox]" in script
    assert 'url = "${CODEX_GATEWAY_URL}"' in script
    assert 'CODEX_ENABLED_TOOLS_JSON="$(load_enabled_tools_toml "${GUEST_SESSION_ID}")"' in script
    assert 'enabled_tools = %s\\n' in script
    assert 'enabled = true' in script
    assert 'if [ -f ~/.agent/config.env ]; then' in script
    assert 'export OPENAI_API_KEY="${OPENAI_API_KEY:-relay-placeholder}"' in script
    assert "append_shell_setup ~/.profile" in script
    assert "append_shell_setup ~/.bashrc" in script
