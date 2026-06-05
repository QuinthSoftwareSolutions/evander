from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_bootstrap_script_enforces_codex_config_and_placeholder_key() -> None:
    script = (ROOT / "scripts" / "bootstrap_codex_vm.sh").read_text(encoding="utf-8")
    guest_ssh_lib = (ROOT / "scripts" / "lib" / "guest_ssh.sh").read_text(encoding="utf-8")

    assert "Usage: bootstrap_codex_vm.sh [--skill NAME ...] <vm-name> [vm-user]" in script
    assert (
        'HOST_CODEX_SKILLS_DIR="${HOST_CODEX_SKILLS_DIR:-${CALLER_HOME}/.codex/skills}"' in script
    )
    assert (
        'HOST_REPO_CODEX_SKILLS_DIR="${HOST_REPO_CODEX_SKILLS_DIR:-${ROOT_DIR}/skills}"' in script
    )
    assert (
        'HOST_CODEX_MODELS_CACHE_PATH="${HOST_CODEX_MODELS_CACHE_PATH:-${CALLER_HOME}/.codex/models_cache.json}"'
        in script
    )
    assert "CODEX_SKILLS=()" in script
    assert "--skill" in script
    assert "resolve_skill_path() {" in script
    assert 'mkdir -p "${CODEX_HOME}/skills"' in script
    assert 'scp "${SSH_OPTS[@]}" -r "${skill_path}"' in script
    assert 'remote_skill_bootstrap_dir="$(' in script
    assert 'printf "%s\\n" "${CODEX_HOME}/skill-bootstrap"' in script
    assert '"${VM_USER}@${VM_IP}:${remote_skill_bootstrap_dir}/"' in script
    assert (
        'mv "${CODEX_HOME}/skill-bootstrap/${SKILL_NAME}" "${CODEX_HOME}/skills/${SKILL_NAME}"'
        in script
    )
    assert "guest_ssh_resolve_vm_ip" in script
    assert 'ip="${AGENT_SANDBOX_VM_IP:-}"' in guest_ssh_lib
    assert "sudo -n virsh domifaddr" in guest_ssh_lib
    assert "pkexec virsh domifaddr" in guest_ssh_lib
    assert 'SSH_USER="${VM_SSH_USER:-agent-admin}"' in script
    assert 'GUEST_BOOTSTRAP_WAIT_SECONDS="${GUEST_BOOTSTRAP_WAIT_SECONDS:-180}"' in script
    assert "cloud-init status --wait" in script
    assert 'install -d -m 0775 -o "${VM_USER}" -g "${agent_group}" /artifacts' in script
    assert 'ln -sfn /artifacts "${agent_home}/artifacts"' in script
    assert 'chown -R "${VM_USER}:${agent_group}" "${agent_home}"' in script
    assert "prepare_guest_home" in script
    assert 'export CODEX_HOME="${CODEX_HOME:-$HOME/.config/agent-sandbox/codex}"' in script
    assert (
        'for CODEX_CONFIG_PATH in "${CODEX_HOME}/config.toml" "${HOME}/.codex/config.toml"; do'
        in script
    )
    assert 'scp "${SSH_OPTS[@]}" "${HOST_CODEX_MODELS_CACHE_PATH}"' in script
    assert 'cp ~/codex-models-cache.json "${CODEX_HOME}/models_cache.json"' in script
    assert 'cp ~/codex-models-cache.json "${HOME}/.codex/models_cache.json"' in script
    assert 'cat > "${CODEX_CONFIG_PATH}" <<CONFIG' in script
    assert 'model = "gpt-5.4"' in script
    assert 'model_reasoning_effort = "high"' in script
    assert 'personality = "pragmatic"' in script
    assert 'web_search = "disabled"' in script
    assert (
        'CODEX_LLM_BASE_URL="${LLM_BASE_URL:-${AGENT_SANDBOX_LLM_BASE_URL:-http://192.168.250.1:11434/v1}}"'
        in script
    )
    assert (
        'CODEX_GATEWAY_URL="${AGENT_SANDBOX_GATEWAY_URL:-http://192.168.250.1:8000/mcp}"' in script
    )
    assert 'openai_base_url = "${CODEX_LLM_BASE_URL}"' in script
    assert "[mcp_servers.agent_sandbox]" in script
    assert 'url = "${CODEX_GATEWAY_URL}"' in script
    assert "enabled_tools = %s\\n" not in script
    assert "load_enabled_tools_toml" not in script
    assert "azure_openai_endpoint" not in script
    assert "azure_openai_api_key_env" not in script
    assert "azure_openai_model_deployments" not in script
    assert "https://example-resource.openai.azure.com" not in script
    assert "AZURE_OPENAI_API_KEY" not in script
    assert "deployment-gpt-5-4" not in script
    assert "enabled = true" in script
    assert "if [ -f ~/.agent/config.env ]; then" in script
    assert "if [ -f /etc/agent-sandbox/session.env ]; then" in script
    assert 'export OPENAI_API_KEY="${OPENAI_API_KEY:-relay-placeholder}"' in script
    assert 'if [ -n "${AGENT_SANDBOX_SESSION_ID:-}" ]; then' in script
    assert (
        'CODEX_LLM_BASE_URL="${CODEX_LLM_BASE_URL%/}/session/${AGENT_SANDBOX_SESSION_ID}"' in script
    )
    assert (
        'CODEX_GATEWAY_URL="${CODEX_GATEWAY_URL}?session_id=${AGENT_SANDBOX_SESSION_ID}"' in script
    )
    assert "if line.strip() not in {" in script
    assert "append_shell_setup ~/.profile" in script
    assert "append_shell_setup ~/.bashrc" in script
    assert 'ENABLE_GUEST_HEARTBEAT="${ENABLE_GUEST_HEARTBEAT:-0}"' in script
    assert 'AGENT_MODEL="${AGENT_MODEL:-}"' in script
    assert (
        'GUEST_HEARTBEAT_PROMPT="${GUEST_HEARTBEAT_PROMPT:-Resume work for this session.' in script
    )
    assert "AGENT_MODEL='${AGENT_MODEL}' bash -s" in script
    assert (
        'AGENT_MODEL=$(python3 -c \'import os, shlex; print(shlex.quote(os.environ.get("AGENT_MODEL", "")))\')'
        in script
    )
    assert "systemctl --user disable --now agent-heartbeat.timer" in script
    assert "systemctl --user enable --now agent-heartbeat.timer" in script


def test_bootstrap_copilot_script_writes_mcp_config_and_auth_guidance() -> None:
    script = (ROOT / "scripts" / "bootstrap_copilot_vm.sh").read_text(encoding="utf-8")

    assert "command -v copilot" in script
    assert "cat > ~/.copilot/config.json <<CONFIG" in script
    assert "cat > ~/.copilot/mcp-config.json <<CONFIG" in script
    assert "cat > ~/bin/gh <<SHIM" in script
    assert 'COPILOT_HOST_AUTH_URL="${COPILOT_GATEWAY_BASE_URL}/copilot-auth/token"' in script
    assert (
        'python3 - "${COPILOT_HOST_AUTH_URL}" "\\${AGENT_SANDBOX_SESSION_ID:-}" <<\'PY\'' in script
    )
    assert '"\\${COPILOT_HOST_AUTH_URL}"' not in script
    assert '"X-Agent-Sandbox-Session-Id": session_id' in script
    assert "gh auth token" in script
    assert '"mcpServers": {' in script
    assert '"agent_sandbox": {' in script
    assert '"type": "http"' in script
    assert (
        'COPILOT_GATEWAY_URL="${AGENT_SANDBOX_GATEWAY_URL:-http://192.168.250.1:8000/mcp}"'
        in script
    )
    assert 'if [ -n "${AGENT_SANDBOX_SESSION_ID:-}" ]; then' in script
    assert (
        'COPILOT_GATEWAY_URL="${COPILOT_GATEWAY_URL}?session_id=${AGENT_SANDBOX_SESSION_ID}"'
        in script
    )
    assert 'export COPILOT_HOME="${COPILOT_HOME:-$HOME/.copilot}"' in script
    assert "copilot version" in script
    assert "host gateway" in script
    assert "COPILOT_GITHUB_TOKEN" in script
    assert "GH_TOKEN" in script
    assert "GITHUB_TOKEN" in script


def test_launch_script_can_derive_public_key_from_private_key() -> None:
    script = (ROOT / "scripts" / "launch_agent_vm.sh").read_text(encoding="utf-8")

    assert (
        'HTTP_TARGET_RELAY_BASE_URL="${HTTP_TARGET_RELAY_BASE_URL:-http://192.168.250.1:11777}"'
        in script
    )
    assert (
        '-e "s|__HTTP_TARGET_RELAY_BASE_URL__|${ESCAPED_HTTP_TARGET_RELAY_BASE_URL}|g" \\' in script
    )
    assert 'ANTHROPIC_BASE_URL="${ANTHROPIC_BASE_URL:-http://192.168.250.1:11434}"' in script
    assert (
        '-e "s|__ANTHROPIC_BASE_URL__|${ESCAPED_ANTHROPIC_BASE_URL}|g" \\' in script
    )
    assert (
        'SSH_PRIVATE_KEY_PATH="${SSH_PRIVATE_KEY_PATH:-${CALLER_HOME}/.ssh/id_ed25519}"' in script
    )
    assert 'ssh-keygen -y -f "${SSH_PRIVATE_KEY_PATH}" > "${DERIVED_PUBLIC_KEY_FILE}"' in script
    assert (
        "Alternatively set SSH_PRIVATE_KEY_PATH to derive the public key automatically." in script
    )


def test_claude_bootstrap_script_writes_relay_and_mcp_config() -> None:
    script = (ROOT / "scripts" / "bootstrap_claude_vm.sh").read_text(encoding="utf-8")

    assert "Usage: bootstrap_claude_vm.sh <vm-name> [vm-user]" in script
    assert "HOST_CLAUDE_BIN" in script
    assert "ANTHROPIC_BASE_URL" in script
    assert "ANTHROPIC_AUTH_TOKEN" in script
    assert "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY" in script
    assert "DISABLE_TELEMETRY" in script
    assert '"type": "http"' in script
    assert '"tools": ["*"]' in script
    assert 'export AGENT_PROVIDER="${AGENT_PROVIDER:-claude}"' in script
    assert "claude --version" in script


def test_guest_heartbeat_assets_exist() -> None:
    root = ROOT / "scripts" / "guest"

    cycle_script = (root / "heartbeat_cycle.sh").read_text(encoding="utf-8")
    service = (root / "agent-heartbeat.service").read_text(encoding="utf-8")
    timer = (root / "agent-heartbeat.timer").read_text(encoding="utf-8")

    assert (
        'AGENT_SANDBOX_GUEST_HEARTBEAT_PROMPT="${AGENT_SANDBOX_GUEST_HEARTBEAT_PROMPT:-'
        in cycle_script
    )
    assert 'HEARTBEAT_ENV_FILE="${HOME}/.config/agent-sandbox/guest-heartbeat.env"' in cycle_script
    assert '. "${HEARTBEAT_ENV_FILE}"' in cycle_script
    assert "agent_runner_resume_last" in cycle_script
    assert "agent-*.state" in cycle_script
    assert "codex-resume-last.state" in cycle_script
    assert "EnvironmentFile=-%h/.config/agent-sandbox/guest-heartbeat.env" in service
    assert "ExecStart=%h/.config/systemd/user/heartbeat_cycle.sh" in service
    assert "OnUnitActiveSec=5min" in timer
    assert "Unit=agent-heartbeat.service" in timer


def test_node_bootstraps_preserve_home_bin_precedence() -> None:
    playwright = (ROOT / "scripts" / "bootstrap_playwright_vm.sh").read_text(encoding="utf-8")
    frontend = (ROOT / "scripts" / "bootstrap_frontend_deps_vm.sh").read_text(encoding="utf-8")

    assert 'export PATH="$HOME/bin:$HOME/toolchains/node-v24.14.0/bin:$PATH"' in playwright
    assert 'export PATH="$HOME/bin:$HOME/toolchains/node-v24.14.0/bin:$PATH"' in frontend
    assert 'export PATH="$HOME/toolchains/node-v24.14.0/bin:$PATH"' in playwright
    assert 'export PATH="$HOME/toolchains/node-v24.14.0/bin:$PATH"' in frontend


def test_setup_vm_supports_codex_skills() -> None:
    script = (ROOT / "scripts" / "setup_vm.sh").read_text(encoding="utf-8")

    assert "--codex-skill" in script
    assert "BOOTSTRAP_CODEX_SKILLS=()" in script
    assert "--codex-skill requires AGENT_PROVIDER=codex." in script
    assert 'BOOTSTRAP_PROVIDER_ARGS+=("--skill" "${skill}")' in script
    assert "HTTP_TARGET_RELAY_BASE_URL=${HTTP_TARGET_RELAY_BASE_URL:-}" in script


def test_repo_local_app_multi_env_skill_exists() -> None:
    skill = (ROOT / "skills" / "exampleindustry-app-multi-env-demo" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "name: exampleindustry-app-multi-env-demo" in skill
    assert "run_script(...)" in skill
    assert "HTTP_TARGET_RELAY_BASE_URL" in skill
    assert "HTTP target relay" in skill
    assert "environment alias" in skill
    assert "Please create a APP demo" in skill
    assert "Use this skill by default for sandboxed agents" in skill
    assert "APP_API_TARGET_ALIAS" in skill
    assert "APP_UI_TARGET_ALIAS" in skill
    assert "OPENAI_BASE_URL" in skill
    assert "imagegen" in skill
    assert "never guess between multiple demo environments by recency alone" in skill
