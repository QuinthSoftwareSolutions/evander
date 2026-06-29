from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def run_subprocess(
    cmd: list[str],
    *,
    check: bool = False,
    capture_output: bool = True,
    text: bool = True,
    timeout: float | None = None,
    input: str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        check=check,
        capture_output=capture_output,
        text=text,
        timeout=timeout,
        input=input,
        env=env,
    )


def combined_process_message(stdout: str | None, stderr: str | None) -> str:
    stdout_text = (stdout or "").strip()
    stderr_text = (stderr or "").strip()
    if stdout_text and stderr_text:
        if stdout_text == stderr_text:
            return stdout_text
        return f"{stdout_text}\n{stderr_text}"
    return stdout_text or stderr_text


def python_has_pip(python_bin: str) -> bool:
    version_check = run_subprocess([python_bin, "--version"], capture_output=True, text=True)
    if version_check.returncode != 0:
        return False
    pip_check = run_subprocess(
        [python_bin, "-m", "pip", "--version"], capture_output=True, text=True
    )
    return pip_check.returncode == 0


def ensure_python_has_pip(python_bin: str) -> bool:
    if python_has_pip(python_bin):
        return True
    ensurepip_result = run_subprocess(
        [python_bin, "-m", "ensurepip", "--upgrade", "--default-pip"],
        capture_output=True,
        text=True,
    )
    if ensurepip_result.returncode != 0:
        return False
    return python_has_pip(python_bin)


def resolve_host_python_with_pip() -> str:
    configured = os.environ.get("HOST_PYTHON_BIN", "").strip()
    if configured:
        if ensure_python_has_pip(configured):
            return configured
        raise RuntimeError(f"Host Python must provide pip: {configured}")

    candidates: list[str] = []
    if sys.executable:
        candidates.append(sys.executable)
    candidates.extend(["python3", "python"])

    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if ensure_python_has_pip(candidate):
            return candidate

    preferred = sys.executable or "python3"
    raise RuntimeError(f"Host Python must provide pip: {preferred}")


def resolve_path_setting(*, base_dir: Path, value: object | None, fallback: Path) -> Path:
    if value is None:
        return fallback
    text = str(value).strip()
    if not text:
        return fallback
    path = Path(text)
    if not path.is_absolute():
        path = base_dir / path
    return path


def pkexec_env_command(
    base_cmd: list[str], env: dict[str, str], allowed_keys: tuple[str, ...]
) -> list[str]:
    cmd = ["pkexec", "env"]
    for key in allowed_keys:
        value = env.get(key)
        if value:
            cmd.append(f"{key}={value}")
    cmd.extend(base_cmd)
    return cmd


def sudo_env_command(
    base_cmd: list[str], env: dict[str, str], allowed_keys: tuple[str, ...]
) -> list[str]:
    preserved_keys = [key for key in allowed_keys if env.get(key)]
    cmd = ["sudo", "-n"]
    if preserved_keys:
        cmd.append(f"--preserve-env={','.join(preserved_keys)}")
    cmd.extend(base_cmd)
    return cmd


def can_prompt_for_privileges() -> bool:
    if sys.stdin is not None and sys.stdin.isatty():
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


_run_subprocess = run_subprocess
_combined_process_message = combined_process_message
_resolve_host_python_with_pip = resolve_host_python_with_pip
_resolve_path_setting = resolve_path_setting
_pkexec_env_command = pkexec_env_command
_sudo_env_command = sudo_env_command
_can_prompt_for_privileges = can_prompt_for_privileges
