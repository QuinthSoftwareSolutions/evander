from __future__ import annotations

import base64
import os
import shlex
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _copy_runtime_config_sandbox(tmp_path: Path) -> Path:
    sandbox_root = tmp_path / "sandbox"
    lib_dir = sandbox_root / "scripts" / "lib"
    lib_dir.mkdir(parents=True)
    runtime_config = ROOT / "scripts" / "lib" / "runtime_config.sh"
    (lib_dir / "runtime_config.sh").write_text(
        runtime_config.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return sandbox_root


def _run_load_root_secrets_env(
    sandbox_root: Path,
    *,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    script = f"""
set -euo pipefail
ROOT_DIR={shlex.quote(str(sandbox_root))}
source {shlex.quote(str(sandbox_root / "scripts" / "lib" / "runtime_config.sh"))}
load_root_secrets_env
printf 'AZURE_DEVOPS_PR_PAT=%s\\n' "${{AZURE_DEVOPS_PR_PAT:-}}"
"""
    command_env = os.environ.copy()
    command_env.pop("AZURE_DEVOPS_PR_PAT", None)
    command_env.pop("EVANDER_SECRETS_ENV_FILE", None)
    command_env.pop("EVANDER_SECRETS_ENV_FILE_B64", None)
    if env:
        command_env.update(env)
    return subprocess.run(
        ["bash", "-c", script],
        check=False,
        text=True,
        capture_output=True,
        env=command_env,
    )


def test_load_root_secrets_env_loads_repo_default(tmp_path: Path) -> None:
    sandbox_root = _copy_runtime_config_sandbox(tmp_path)
    (sandbox_root / "secrets.env").write_text(
        'AZURE_DEVOPS_PR_PAT="from-repo-secrets"\n',
        encoding="utf-8",
    )

    result = _run_load_root_secrets_env(sandbox_root)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "AZURE_DEVOPS_PR_PAT=from-repo-secrets\n"


def test_load_root_secrets_env_uses_configured_file(tmp_path: Path) -> None:
    sandbox_root = _copy_runtime_config_sandbox(tmp_path)
    (sandbox_root / "secrets.env").write_text(
        'AZURE_DEVOPS_PR_PAT="from-repo-secrets"\n',
        encoding="utf-8",
    )
    configured = tmp_path / "configured-secrets.env"
    configured.write_text('AZURE_DEVOPS_PR_PAT="from-configured-secrets"\n', encoding="utf-8")

    result = _run_load_root_secrets_env(
        sandbox_root,
        env={"EVANDER_SECRETS_ENV_FILE": str(configured)},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "AZURE_DEVOPS_PR_PAT=from-configured-secrets\n"


def test_load_root_secrets_env_b64_takes_precedence(tmp_path: Path) -> None:
    sandbox_root = _copy_runtime_config_sandbox(tmp_path)
    configured = tmp_path / "configured-secrets.env"
    configured.write_text('AZURE_DEVOPS_PR_PAT="from-configured-secrets"\n', encoding="utf-8")
    encoded = tmp_path / "encoded-secrets.env"
    encoded.write_text('AZURE_DEVOPS_PR_PAT="from-encoded-secrets"\n', encoding="utf-8")

    result = _run_load_root_secrets_env(
        sandbox_root,
        env={
            "EVANDER_SECRETS_ENV_FILE": str(configured),
            "EVANDER_SECRETS_ENV_FILE_B64": base64.b64encode(
                str(encoded).encode("utf-8")
            ).decode("ascii"),
        },
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "AZURE_DEVOPS_PR_PAT=from-encoded-secrets\n"


def test_load_root_secrets_env_missing_configured_file_is_nonfatal(tmp_path: Path) -> None:
    sandbox_root = _copy_runtime_config_sandbox(tmp_path)

    result = _run_load_root_secrets_env(
        sandbox_root,
        env={"EVANDER_SECRETS_ENV_FILE": str(tmp_path / "missing-secrets.env")},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "AZURE_DEVOPS_PR_PAT=\n"


def test_load_root_secrets_env_unreadable_configured_file_fails(tmp_path: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("root can read chmod 000 files")
    sandbox_root = _copy_runtime_config_sandbox(tmp_path)
    configured = tmp_path / "unreadable-secrets.env"
    configured.write_text('AZURE_DEVOPS_PR_PAT="from-unreadable-secrets"\n', encoding="utf-8")
    configured.chmod(0)

    try:
        result = _run_load_root_secrets_env(
            sandbox_root,
            env={"EVANDER_SECRETS_ENV_FILE": str(configured)},
        )
    finally:
        configured.chmod(0o600)

    assert result.returncode != 0
    assert "Configured secrets env file is not readable" in result.stderr
