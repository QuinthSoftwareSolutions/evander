from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from typing import Any


_ALLOWED_ENV_KEYS = {
    "SSH_PUBLIC_KEY_FILE",
    "SSH_PRIVATE_KEY_PATH",
    "GATEWAY_URL",
    "LLM_BASE_URL",
    "AGENT_MODEL",
    "AGENT_PROVIDER",
    "HOST_CLAUDE_BIN",
    "LIBVIRT_BASE_IMAGE",
    "NUGET_RELAY_BASE_URL",
    "EVANDER_RUNTIME_DIR",
    "VM_STORAGE_DIR",
    "SHARED_NUGET_CACHE_DIR",
    "SHARED_NUGET_CACHE_TAG",
    "SHARED_NUGET_CACHE_MODE",
    "RAM_MB",
    "VCPUS",
    "DISK_SIZE_GB",
    "VM_WAIT_IP_TIMEOUT_SECONDS",
    "VM_WAIT_SSH_TIMEOUT_SECONDS",
    "MSSQL_SA_PASSWORD",
}


def _emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def _run(cmd: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def _resolve_vm_ip(vm_name: str) -> str | None:
    result = _run(["virsh", "domifaddr", vm_name])
    if result.returncode != 0:
        return None
    for line in (result.stdout or "").splitlines():
        parts = line.split()
        if "ipv4" in parts and len(parts) >= 4:
            return parts[3].split("/", 1)[0]
    return None


def _wait_for_vm_ip(vm_name: str, timeout_seconds: int = 180) -> str | None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        ip = _resolve_vm_ip(vm_name)
        if ip:
            return ip
        time.sleep(2)
    return None


def _filtered_env(raw_env: object) -> dict[str, str]:
    env = os.environ.copy()
    if isinstance(raw_env, dict):
        for key, value in raw_env.items():
            if key in _ALLOWED_ENV_KEYS and isinstance(value, str) and value:
                env[key] = value
    return env


def _handle(payload: dict[str, Any]) -> dict[str, Any]:
    op = str(payload.get("op") or "").strip()
    if op == "ping":
        return {"ok": True, "status": "pong"}
    if op == "exit":
        return {"ok": True}
    if op == "launch_vm":
        script_path = str(payload["script_path"])
        vm_name = str(payload["vm_name"])
        session_id = str(payload["session_id"])
        result = _run([script_path, vm_name, session_id], env=_filtered_env(payload.get("env")))
        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "error": (result.stderr or result.stdout or "").strip()
            if result.returncode != 0
            else None,
        }
    if op == "destroy_vm":
        script_path = str(payload["script_path"])
        vm_name = str(payload["vm_name"])
        result = _run([script_path, vm_name])
        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "error": (result.stderr or result.stdout or "").strip()
            if result.returncode != 0
            else None,
        }
    if op == "run_script":
        script_path = str(payload["script_path"])
        args = [str(item) for item in payload.get("args") or []]
        result = _run([script_path, *args], env=_filtered_env(payload.get("env")))
        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "error": (result.stderr or result.stdout or "").strip()
            if result.returncode != 0
            else None,
        }
    if op == "virsh_domifaddr":
        vm_name = str(payload["vm_name"])
        ip = _wait_for_vm_ip(vm_name)
        return {
            "ok": bool(ip),
            "ip": ip,
            "error": None
            if ip
            else f"Timed out waiting for VM {vm_name} to receive an IPv4 address.",
        }
    return {"ok": False, "error": f"Unknown operation: {op}"}


def main() -> None:
    for line in sys.stdin:
        raw = line.strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("request must be a JSON object")
            response = _handle(payload)
        except Exception as exc:
            response = {"ok": False, "error": str(exc)}
        _emit(response)
        if payload.get("op") == "exit":
            break


if __name__ == "__main__":
    main()
