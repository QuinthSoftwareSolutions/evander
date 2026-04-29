from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_sandbox.config import GatewayConfig
from agent_sandbox.effects.providers.base import EffectRuntimeContext
from agent_sandbox.effects.providers.bash_script import BashScriptEffectProvider


def _runtime_context(tmp_path: Path) -> EffectRuntimeContext:
    return EffectRuntimeContext(
        resolved_config=GatewayConfig(runtime_dir=tmp_path / "runtime"),
        admin_service=SimpleNamespace(),
        env={"BASE_ENV": "from-context"},
    )


def _effect_definition() -> SimpleNamespace:
    return SimpleNamespace(id="effect-script", display_name="Run script", type="effect:bash:script")


def test_bash_script_provider_executes_script_with_args_and_context(tmp_path: Path) -> None:
    script_path = tmp_path / "echo-inputs.sh"
    script_path.write_text(
        "\n".join(
            [
                "printf 'cwd=%s\\n' \"$PWD\"",
                'printf \'args=%s|%s\\n\' "$1" "$2"',
                'printf \'env=%s|%s\\n\' "${CUSTOM_ENV:-}" "${BASE_ENV:-}"',
                "cat",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    working_directory = tmp_path / "work"
    working_directory.mkdir()

    provider = BashScriptEffectProvider()
    provider.initialize(_effect_definition(), _runtime_context(tmp_path))

    result = provider.execute(
        resolved_config={
            "script_path": str(script_path),
            "args": ["--flag", "value with spaces"],
            "working_directory": str(working_directory),
            "environment": {"CUSTOM_ENV": "hello"},
            "stdin": "payload\n",
            "check": True,
        }
    )

    assert result["success"] is True
    assert result["action"] == "bash_script"
    assert result["script_path"] == str(script_path)
    assert result["exit_code"] == 0
    assert result["stdout"] == (
        f"cwd={working_directory}\nargs=--flag|value with spaces\nenv=hello|from-context\npayload"
    )


def test_bash_script_provider_raises_when_checked_script_fails(tmp_path: Path) -> None:
    script_path = tmp_path / "fail.sh"
    script_path.write_text("echo bad >&2\nexit 7\n", encoding="utf-8")

    provider = BashScriptEffectProvider()
    provider.initialize(_effect_definition(), _runtime_context(tmp_path))

    with pytest.raises(RuntimeError, match="bad"):
        provider.execute(
            resolved_config={
                "script_path": str(script_path),
                "check": True,
            }
        )


def test_bash_script_provider_returns_failure_result_when_check_is_disabled(tmp_path: Path) -> None:
    script_path = tmp_path / "fail-soft.sh"
    script_path.write_text("echo out\necho err >&2\nexit 3\n", encoding="utf-8")

    provider = BashScriptEffectProvider()
    provider.initialize(_effect_definition(), _runtime_context(tmp_path))

    result = provider.execute(
        resolved_config={
            "script_path": str(script_path),
            "check": False,
        }
    )

    assert result["success"] is False
    assert result["exit_code"] == 3
    assert result["stdout"] == "out\n"
    assert result["stderr"] == "err\n"


def test_bash_script_provider_exposes_final_json_object_from_stdout(tmp_path: Path) -> None:
    script_path = tmp_path / "emit-json.sh"
    script_path.write_text(
        "\n".join(
            [
                "echo 'starting deploy'",
                'echo \'{"environment_alias":"demo-a","app_base_url":"https://demo-a.example/api","provisioning_status":"ready"}\'',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    provider = BashScriptEffectProvider()
    provider.initialize(_effect_definition(), _runtime_context(tmp_path))

    result = provider.execute(
        resolved_config={
            "script_path": str(script_path),
            "check": True,
        }
    )

    assert result["success"] is True
    assert result["parsed_output"]["environment_alias"] == "demo-a"
    assert result["environment_alias"] == "demo-a"
    assert result["app_base_url"] == "https://demo-a.example/api"
    assert result["provisioning_status"] == "ready"
