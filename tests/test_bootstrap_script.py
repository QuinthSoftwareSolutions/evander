from pathlib import Path


def test_bootstrap_script_enforces_codex_config_and_placeholder_key() -> None:
    script = Path("/home/jonathan/agent-sandbox/scripts/bootstrap_codex_vm.sh").read_text(
        encoding="utf-8"
    )

    assert "sudo -n virsh domifaddr" in script
    assert 'cat > ~/.codex/config.toml <<CONFIG' in script
    assert 'openai_base_url = "${LLM_BASE_URL}"' in script
    assert 'export OPENAI_API_KEY="${OPENAI_API_KEY:-relay-placeholder}"' in script
    assert "append_shell_setup ~/.profile" in script
    assert "append_shell_setup ~/.bashrc" in script
