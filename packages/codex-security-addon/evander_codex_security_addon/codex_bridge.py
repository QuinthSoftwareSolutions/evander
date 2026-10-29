#!/usr/bin/env python3
"""Codex CLI transport: normal binary for relay jobs, private broker otherwise."""

import json
import os
from pathlib import Path
import socket
import sys
import threading
import time


def local_plugin_setup(arguments, home):
    """Only the SDK's two local install commands bypass the model broker."""
    if not home:
        return False
    root = Path(home).resolve()
    if not any(root.is_relative_to(base) for base in (Path("/artifacts"), Path("/home/agent"))):
        return False
    return (
        arguments == ["plugin", "marketplace", "add", str(root / "sdk-marketplace")]
        or arguments == ["plugin", "add", "--json", "codex-security@codex-security-sdk"]
    )


def main():
    endpoint = os.environ.get("EVANDER_CODEX_BROKER_SOCKET")
    if not endpoint or local_plugin_setup(sys.argv[1:], os.environ.get("CODEX_HOME")):
        binary = os.environ.get("EVANDER_NATIVE_CODEX", "/usr/local/bin/codex")
        if not os.path.isfile(binary):
            binary = "/home/agent/bin/codex"
        os.execv(binary, [binary, *sys.argv[1:]])
    environment = {
        key: value
        for key, value in os.environ.items()
        if key
        in {
            "CODEX_HOME",
            "CODEX_CLI_PATH",
            "CODEX_SECURITY_SCAN_ROOT",
            "CODEX_SECURITY_STATE_DIR",
            "CODEX_SECURITY_SURFACE",
            "CODEX_SECURITY_CONFIG_PATH",
            "CODEX_SECURITY_DEEP_SCAN_CONFIG_PATH",
            "CODEX_SECURITY_REPOSITORY",
            "CODEX_SECURITY_SCAN_DIR",
            "CODEX_SECURITY_PLUGIN_ROOT",
            "CODEX_SECURITY_TARGET_PATHS_FILE",
            "CODEX_SECURITY_KNOWLEDGE_BASE",
            "CODEX_SECURITY_STARTED_AT",
            "CODEX_SECURITY_SCAN_ID",
            "CODEX_SECURITY_TARGET_ID",
            "CODEX_SECURITY_TARGET_DISPLAY_NAME",
            "CODEX_SECURITY_TARGET_KIND",
            "CODEX_SECURITY_TARGET_REVISION",
            "CODEX_SECURITY_TARGET_SNAPSHOT_DIGEST",
            "PYTHON",
        }
    }
    client = socket.socket(socket.AF_UNIX)
    deadline = time.monotonic() + 30
    while True:
        try:
            client.connect(endpoint)
            break
        except (FileNotFoundError, ConnectionRefusedError):
            if time.monotonic() >= deadline:
                raise RuntimeError("Worker Codex runtime broker did not become available")
            time.sleep(0.1)
    args = sys.argv[1:]
    if "exec" in args and not sys.stdin.isatty():
        prompt = sys.stdin.read(16 * 1024 * 1024 + 1)
        if len(prompt) > 16 * 1024 * 1024:
            raise ValueError("Codex prompt exceeds size limit")
        if prompt:
            if args[-1:] == ["-"]:
                args[-1] = prompt
            else:
                args.append(prompt)
    client.sendall(
        (json.dumps({"argv": args, "cwd": os.getcwd(), "environment": environment}) + "\n").encode()
    )

    def input_frames():
        try:
            for line in sys.stdin.buffer:
                client.sendall(line)
            client.shutdown(socket.SHUT_WR)
        except OSError:
            pass

    if "app-server" in sys.argv:
        threading.Thread(target=input_frames, daemon=True).start()
    with client, client.makefile("rb") as stream:
        for line in stream:
            frame = json.loads(line)
            if "exit_code" in frame:
                return int(frame["exit_code"])
            destination = sys.stdout if frame["stream"] == "stdout" else sys.stderr
            destination.write(frame["text"])
            destination.flush()
    return 0 if "app-server" in sys.argv else 1


if __name__ == "__main__":
    sys.exit(main())
