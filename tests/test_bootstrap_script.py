from pathlib import Path


def test_bootstrap_script_enforces_codex_config_and_placeholder_key() -> None:
    script = Path("/home/jonathan/agent-sandbox/scripts/bootstrap_codex_vm.sh").read_text(
        encoding="utf-8"
    )

    assert "sudo -n virsh domifaddr" in script
    assert 'cat > ~/.codex/config.toml <<CONFIG' in script
    assert 'model = "gpt-5.4"' in script
    assert 'model_reasoning_effort = "high"' in script
    assert 'personality = "pragmatic"' in script
    assert 'CODEX_LLM_BASE_URL="${LLM_BASE_URL:-${AGENT_SANDBOX_LLM_BASE_URL:-http://192.168.250.1:11434/v1}}"' in script
    assert 'CODEX_GATEWAY_URL="${AGENT_SANDBOX_GATEWAY_URL:-http://192.168.250.1:8000/mcp}"' in script
    assert 'openai_base_url = "${CODEX_LLM_BASE_URL}"' in script
    assert "[mcp_servers.agent_sandbox]" in script
    assert 'url = "${CODEX_GATEWAY_URL}"' in script
    assert 'enabled_tools = %s\\n' not in script
    assert 'load_enabled_tools_toml' not in script
    assert 'enabled = true' in script
    assert 'if [ -f ~/.agent/config.env ]; then' in script
    assert 'export OPENAI_API_KEY="${OPENAI_API_KEY:-relay-placeholder}"' in script
    assert "append_shell_setup ~/.profile" in script
    assert "append_shell_setup ~/.bashrc" in script
    assert 'ENABLE_GUEST_HEARTBEAT="${ENABLE_GUEST_HEARTBEAT:-0}"' in script
    assert 'GUEST_HEARTBEAT_PROMPT="${GUEST_HEARTBEAT_PROMPT:-Resume work for this session.' in script
    assert 'systemctl --user disable --now agent-heartbeat.timer' in script
    assert 'systemctl --user enable --now agent-heartbeat.timer' in script


def test_guest_heartbeat_assets_exist() -> None:
    root = Path("/home/jonathan/agent-sandbox/scripts/guest")

    cycle_script = (root / "heartbeat_cycle.sh").read_text(encoding="utf-8")
    service = (root / "agent-heartbeat.service").read_text(encoding="utf-8")
    timer = (root / "agent-heartbeat.timer").read_text(encoding="utf-8")

    assert 'AGENT_SANDBOX_GUEST_HEARTBEAT_PROMPT="${AGENT_SANDBOX_GUEST_HEARTBEAT_PROMPT:-' in cycle_script
    assert "agent_runner_resume_last" in cycle_script
    assert "agent-*.state" in cycle_script
    assert "EnvironmentFile=-%h/.config/agent-sandbox/guest-heartbeat.env" in service
    assert "ExecStart=%h/.config/systemd/user/heartbeat_cycle.sh" in service
    assert "OnUnitActiveSec=5min" in timer
    assert "Unit=agent-heartbeat.service" in timer
