from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_bootstrap_script_enforces_codex_config_and_placeholder_key() -> None:
    script = (ROOT / "scripts" / "bootstrap_codex_vm.sh").read_text(encoding="utf-8")
    guest_ssh_lib = (ROOT / "scripts" / "lib" / "guest_ssh.sh").read_text(encoding="utf-8")

    assert "guest_ssh_resolve_vm_ip" in script
    assert 'ip="${AGENT_SANDBOX_VM_IP:-}"' in guest_ssh_lib
    assert "sudo -n virsh domifaddr" in guest_ssh_lib
    assert "pkexec virsh domifaddr" in guest_ssh_lib
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
    assert 'if [ -f /etc/agent-sandbox/session.env ]; then' in script
    assert 'export OPENAI_API_KEY="${OPENAI_API_KEY:-relay-placeholder}"' in script
    assert 'if [ -n "${AGENT_SANDBOX_SESSION_ID:-}" ]; then' in script
    assert 'CODEX_LLM_BASE_URL="${CODEX_LLM_BASE_URL%/}/session/${AGENT_SANDBOX_SESSION_ID}"' in script
    assert 'CODEX_GATEWAY_URL="${CODEX_GATEWAY_URL}?session_id=${AGENT_SANDBOX_SESSION_ID}"' in script
    assert "append_shell_setup ~/.profile" in script
    assert "append_shell_setup ~/.bashrc" in script
    assert 'ENABLE_GUEST_HEARTBEAT="${ENABLE_GUEST_HEARTBEAT:-0}"' in script
    assert 'AGENT_MODEL="${AGENT_MODEL:-}"' in script
    assert 'GUEST_HEARTBEAT_PROMPT="${GUEST_HEARTBEAT_PROMPT:-Resume work for this session.' in script
    assert "AGENT_MODEL='${AGENT_MODEL}' bash -s" in script
    assert 'AGENT_MODEL=$(python3 -c \'import os, shlex; print(shlex.quote(os.environ.get("AGENT_MODEL", "")))\')' in script
    assert 'systemctl --user disable --now agent-heartbeat.timer' in script
    assert 'systemctl --user enable --now agent-heartbeat.timer' in script


def test_bootstrap_copilot_script_writes_mcp_config_and_auth_guidance() -> None:
    script = (ROOT / "scripts" / "bootstrap_copilot_vm.sh").read_text(encoding="utf-8")

    assert "command -v copilot" in script
    assert 'cat > ~/.copilot/config.json <<CONFIG' in script
    assert 'cat > ~/.copilot/mcp-config.json <<CONFIG' in script
    assert 'cat > ~/bin/gh <<SHIM' in script
    assert 'COPILOT_HOST_AUTH_URL="${COPILOT_GATEWAY_BASE_URL}/copilot-auth/token"' in script
    assert 'python3 - "${COPILOT_HOST_AUTH_URL}" "\\${AGENT_SANDBOX_SESSION_ID:-}" <<\'PY\'' in script
    assert '"\\${COPILOT_HOST_AUTH_URL}"' not in script
    assert '"X-Agent-Sandbox-Session-Id": session_id' in script
    assert "gh auth token" in script
    assert '"mcpServers": {' in script
    assert '"agent_sandbox": {' in script
    assert '"type": "http"' in script
    assert 'COPILOT_GATEWAY_URL="${AGENT_SANDBOX_GATEWAY_URL:-http://192.168.250.1:8000/mcp}"' in script
    assert 'if [ -n "${AGENT_SANDBOX_SESSION_ID:-}" ]; then' in script
    assert 'COPILOT_GATEWAY_URL="${COPILOT_GATEWAY_URL}?session_id=${AGENT_SANDBOX_SESSION_ID}"' in script
    assert 'export COPILOT_HOME="${COPILOT_HOME:-$HOME/.copilot}"' in script
    assert "copilot version" in script
    assert "host gateway" in script
    assert "COPILOT_GITHUB_TOKEN" in script
    assert "GH_TOKEN" in script
    assert "GITHUB_TOKEN" in script


def test_launch_script_can_derive_public_key_from_private_key() -> None:
    script = (ROOT / "scripts" / "launch_agent_vm.sh").read_text(encoding="utf-8")

    assert 'SSH_PRIVATE_KEY_PATH="${SSH_PRIVATE_KEY_PATH:-${CALLER_HOME}/.ssh/id_ed25519}"' in script
    assert 'ssh-keygen -y -f "${SSH_PRIVATE_KEY_PATH}" > "${DERIVED_PUBLIC_KEY_FILE}"' in script
    assert 'Alternatively set SSH_PRIVATE_KEY_PATH to derive the public key automatically.' in script


def test_setup_issue_vm_can_derive_public_key_from_private_key() -> None:
    script = (ROOT / "scripts" / "setup_issue_vm.sh").read_text(encoding="utf-8")

    assert 'ssh-keygen -y -f "${SSH_PRIVATE_KEY_PATH}" > "${DERIVED_PUBLIC_KEY_FILE}"' in script
    assert 'Alternatively set SSH_PRIVATE_KEY_PATH to derive the public key automatically.' in script


def test_guest_heartbeat_assets_exist() -> None:
    root = ROOT / "scripts" / "guest"

    cycle_script = (root / "heartbeat_cycle.sh").read_text(encoding="utf-8")
    service = (root / "agent-heartbeat.service").read_text(encoding="utf-8")
    timer = (root / "agent-heartbeat.timer").read_text(encoding="utf-8")

    assert 'AGENT_SANDBOX_GUEST_HEARTBEAT_PROMPT="${AGENT_SANDBOX_GUEST_HEARTBEAT_PROMPT:-' in cycle_script
    assert 'HEARTBEAT_ENV_FILE="${HOME}/.config/agent-sandbox/guest-heartbeat.env"' in cycle_script
    assert '. "${HEARTBEAT_ENV_FILE}"' in cycle_script
    assert "agent_runner_resume_last" in cycle_script
    assert "agent-*.state" in cycle_script
    assert "codex-resume-last.state" in cycle_script
    assert "EnvironmentFile=-%h/.config/agent-sandbox/guest-heartbeat.env" in service
    assert "ExecStart=%h/.config/systemd/user/heartbeat_cycle.sh" in service
    assert "OnUnitActiveSec=5min" in timer
    assert "Unit=agent-heartbeat.service" in timer
