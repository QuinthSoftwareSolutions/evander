"""Job-scoped Codex transport. Authentication exists only in the native process.

The socket is mounted exclusively into its owning container. It neither exposes
tokens nor accepts host commands, profiles, image names or container selectors.
"""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import select
import socket
import subprocess
import threading
import tomllib
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .codex_app_server import CodexAppServerProcess


class CodexRuntimeProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command_prefix: list[str] = Field(min_length=1)
    codex_path: str = "/usr/local/bin/codex"
    max_processes: int = Field(default=12, ge=1, le=64)


def load_runtime_profiles(path: str | None) -> dict[str, CodexRuntimeProfile]:
    if not path:
        return {}
    source = Path(path)
    if (
        source.is_symlink()
        or source.stat().st_mode & 0o022
        or source.stat().st_uid not in {0, os.geteuid()}
    ):
        raise ValueError(
            "Codex runtime profiles must be operator-owned and not group/world writable"
        )
    raw = json.loads(source.read_text())
    profiles = {key: CodexRuntimeProfile.model_validate(value) for key, value in raw.items()}
    for profile in profiles.values():
        if not PurePosixPath(profile.codex_path).is_absolute():
            raise ValueError("Runtime Codex executable must be absolute")
    return profiles


def validate_request(header: dict[str, Any]):
    argv = header.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(arg, str) for arg in argv):
        raise ValueError("Expected Codex arguments")
    cwd = header.get("cwd")
    if not isinstance(cwd, str):
        raise ValueError("Expected container working directory")
    allowed_path(cwd)
    env = header.get("environment", {})
    allowed = {
        "CODEX_HOME",
        "CODEX_CLI_PATH",
        "CODEX_SECURITY_SCAN_ROOT",
        "CODEX_SECURITY_STATE_DIR",
        "CODEX_SECURITY_SURFACE",
        "CODEX_SECURITY_CONFIG_PATH",
        "CODEX_SECURITY_DEEP_SCAN_CONFIG_PATH",
        "PYTHON",
    }
    if not isinstance(env, dict) or set(env) - allowed:
        raise ValueError("Unsupported Codex runtime environment")
    for key, value in env.items():
        if not isinstance(value, str) or "\x00" in value:
            raise ValueError("Invalid runtime environment value")
        if key != "CODEX_SECURITY_SURFACE":
            allowed_path(value, executable=key in {"CODEX_CLI_PATH", "PYTHON"})
    return argv, cwd, env


def allowed_path(value, *, executable=False):
    path = PurePosixPath(value)
    roots = [PurePosixPath("/home/agent"), PurePosixPath("/artifacts")]
    if executable:
        roots += [PurePosixPath("/opt/evander"), PurePosixPath("/usr")]
    if (
        not path.is_absolute()
        or ".." in path.parts
        or not any(path == root or root in path.parents for root in roots)
    ):
        raise ValueError("Codex runtime path escapes the container workspace")


def parse_arguments(argv, cwd):
    """Interpret only the Codex CLI grammar used by managed SDK runtimes."""
    mode = "exec"
    config = {}
    prompt = None
    model = None
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in {"exec", "app-server"}:
            mode = arg
        elif arg in {
            "--json",
            "--experimental-json",
            "--skip-git-repo-check",
            "--stdio",
            "--ephemeral",
        }:
            if arg == "--ephemeral":
                config["_ephemeral"] = True
        elif arg in {
            "--config",
            "-c",
            "--cd",
            "-C",
            "--model",
            "-m",
            "--color",
            "--thread-source",
            "--cyber-access-program",
        }:
            index += 1
            if index == len(argv):
                raise ValueError("Missing Codex argument value")
            value = argv[index]
            if arg in {"--cd", "-C"}:
                allowed_path(value)
                cwd = value
            elif arg in {"--model", "-m"}:
                model = value
            elif arg in {"--config", "-c"}:
                parsed = tomllib.loads(value)
                merge_config(config, parsed)
            elif arg == "--thread-source":
                config["_thread_source"] = value
            elif arg == "--cyber-access-program":
                config["_cyber_access_program"] = value
        elif arg == "resume":
            index += 1
            if index == len(argv):
                raise ValueError("Missing resumed thread identity")
            config["_resume_thread_id"] = argv[index]
        elif arg.startswith("-"):
            raise ValueError(f"Unsupported brokered Codex argument: {arg}")
        elif prompt is None:
            prompt = arg
        else:
            raise ValueError("Unexpected Codex positional argument")
        index += 1
    if config.get("model_provider", "openai") != "openai" or config.get("model_providers"):
        raise ValueError("Subscription runtime cannot change the model provider")
    if any(
        key in config
        for key in ("forced_login_method", "cli_auth_credentials_store", "openai_api_key")
    ):
        raise ValueError("Subscription authentication is owned by the worker")
    if mode == "exec" and prompt is None:
        raise ValueError("Brokered exec requires an explicit prompt")
    return mode, cwd, config, model, prompt


def merge_config(target, source):
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            merge_config(target[key], value)
        else:
            target[key] = value


class CodexRuntimeBridge:
    def __init__(
        self,
        *,
        path: Path,
        command_factory,
        tokens,
        refresh_tokens,
        max_processes=12,
        process_factory=CodexAppServerProcess,
    ):
        self.path = path
        self.command_factory = command_factory
        self.tokens = tokens
        self.refresh_tokens = refresh_tokens
        self.process_factory = process_factory
        self.closed = threading.Event()
        self.capacity = threading.BoundedSemaphore(max_processes)
        self.clients = set()
        self.lock = threading.Lock()
        self.listener = None

    def start(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path.unlink(missing_ok=True)
        self.listener = socket.socket(socket.AF_UNIX)
        self.listener.bind(str(self.path))
        self.path.chmod(0o666)  # Reachable only through the job's private bind mount.
        self.listener.listen(16)
        self.listener.settimeout(0.5)
        threading.Thread(target=self._accept, name="codex-runtime-broker", daemon=True).start()

    def close(self):
        self.closed.set()
        if self.listener:
            self.listener.close()
        with self.lock:
            clients = list(self.clients)
        for client in clients:
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.path.unlink(missing_ok=True)

    def _accept(self):
        while not self.closed.is_set():
            try:
                client, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            if not self.capacity.acquire(blocking=False):
                try:
                    client.sendall(
                        b'{"stream":"stderr","text":"Codex runtime capacity exhausted\\n"}\n'
                        b'{"exit_code":1}\n'
                    )
                except OSError:
                    pass
                client.close()
                continue
            with self.lock:
                self.clients.add(client)
            threading.Thread(target=self._serve, args=(client,), daemon=True).start()

    def _serve(self, client):
        process = None
        send_lock = threading.Lock()

        def send(value):
            data = (json.dumps(value) + "\n").encode()
            with send_lock:
                client.sendall(data)

        try:
            client.settimeout(30)
            reader = client.makefile("rb")
            line = reader.readline(16 * 1024 * 1024 + 1)
            if len(line) > 16 * 1024 * 1024:
                raise ValueError("Codex request exceeds size limit")
            argv, cwd, env = validate_request(json.loads(line))
            client.settimeout(None)
            if argv == ["--version"]:
                command = self.command_factory(env, [])
                result = subprocess.run(
                    [*command[:-2], "--version"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=True,
                )
                send({"stream": "stdout", "text": result.stdout})
                send({"exit_code": 0})
                return
            mode, cwd, config, model, prompt = parse_arguments(argv, cwd)
            overrides = [
                argv[index + 1] for index, arg in enumerate(argv[:-1]) if arg in {"-c", "--config"}
            ]
            done = threading.Event()
            terminal = {}
            usage = {}

            def notification(method, params):
                if mode == "app-server":
                    send(
                        {
                            "stream": "stdout",
                            "text": json.dumps({"method": method, "params": params}) + "\n",
                        }
                    )
                    return
                if method == "thread/tokenUsage/updated":
                    usage.update(params.get("tokenUsage", {}).get("last", {}))
                if method == "item/completed":
                    item = params.get("item", {})
                    if item.get("type") == "agentMessage":
                        send(
                            {
                                "stream": "stdout",
                                "text": json.dumps(
                                    {
                                        "type": "item.completed",
                                        "item": {
                                            "id": item.get("id", "message"),
                                            "type": "agent_message",
                                            "text": item.get("text", ""),
                                        },
                                    }
                                )
                                + "\n",
                            }
                        )
                if method == "turn/completed":
                    terminal.update(params.get("turn", {}))
                    done.set()

            bound = self.tokens.account_id
            credentials = [self.tokens]

            def refresh(params):
                value = self.refresh_tokens(
                    previous_access_token=credentials[0].access_token,
                    previous_account_id=params.get("previousAccountId"),
                )
                if value.account_id != bound:
                    raise ValueError("Subscription account changed during runtime execution")
                credentials[0] = value
                return value.refresh_response()

            process = self.process_factory(
                self.command_factory(env, overrides),
                server_request_handlers={"account/chatgptAuthTokens/refresh": refresh},
                notification_handler=notification,
                stderr_handler=lambda line: None,
            )
            process.start()
            process.request("account/login/start", self.tokens.app_server_login_params())
            if mode == "app-server":
                while line := reader.readline(16 * 1024 * 1024 + 1):
                    if len(line) > 16 * 1024 * 1024:
                        raise ValueError("Codex frame exceeds size limit")
                    message = json.loads(line)
                    method = message.get("method", "")
                    if method.startswith("account/login") or method in {
                        "account/logout",
                        "account/chatgptAuthTokens/refresh",
                    }:
                        raise ValueError("Runtime clients cannot change authentication")
                    if method == "initialize":
                        result = process.initialization_result
                    elif method == "initialized":
                        continue
                    elif "id" not in message:
                        process.notify(method, message.get("params"))
                        continue
                    else:
                        result = process.request(method, message.get("params"))
                    send(
                        {
                            "stream": "stdout",
                            "text": json.dumps({"id": message["id"], "result": result}) + "\n",
                        }
                    )
            else:
                resume_thread_id = config.pop("_resume_thread_id", None)
                thread_source = config.pop("_thread_source", None)
                ephemeral = config.pop("_ephemeral", False)
                cyber_access_program = config.pop("_cyber_access_program", None)
                thread = process.request(
                    "thread/resume" if resume_thread_id else "thread/start",
                    {
                        "cwd": cwd,
                        "approvalPolicy": "never",
                        "config": config,
                        "modelProvider": "openai",
                        **(
                            {"permissions": config["default_permissions"]}
                            if config.get("default_permissions")
                            else {}
                        ),
                        **(
                            {"threadSource": thread_source}
                            if thread_source and not resume_thread_id
                            else {}
                        ),
                        **({"ephemeral": True} if ephemeral and not resume_thread_id else {}),
                        **({"model": model} if model else {}),
                        **({"threadId": resume_thread_id} if resume_thread_id else {}),
                    },
                )
                thread_id = thread["thread"]["id"]
                send(
                    {
                        "stream": "stdout",
                        "text": json.dumps({"type": "thread.started", "thread_id": thread_id})
                        + "\n",
                    }
                )
                process.request(
                    "turn/start",
                    {
                        "threadId": thread_id,
                        "cwd": cwd,
                        "approvalPolicy": "never",
                        "input": [{"type": "text", "text": prompt}],
                        **(
                            {"cyberAccessProgram": cyber_access_program}
                            if cyber_access_program
                            else {}
                        ),
                    },
                )
                while not done.wait(0.2):
                    if self.closed.is_set() or not process.running:
                        raise RuntimeError("Brokered Codex process was interrupted")
                    if select.select([client], [], [], 0)[0] and not client.recv(
                        1, socket.MSG_PEEK
                    ):
                        return
                ok = terminal.get("status") == "completed"
                event = (
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": usage.get("inputTokens", 0),
                            "cached_input_tokens": usage.get("cachedInputTokens", 0),
                            "output_tokens": usage.get("outputTokens", 0),
                        },
                    }
                    if ok
                    else {"type": "turn.failed", "error": {"message": "Brokered Codex turn failed"}}
                )
                send({"stream": "stdout", "text": json.dumps(event) + "\n"})
                send({"exit_code": 0 if ok else 1})
        except Exception as exc:
            # Never return native exception text: native auth failures may contain tokens.
            try:
                send(
                    {
                        "stream": "stderr",
                        "text": f"Managed Codex runtime failed ({type(exc).__name__})\n",
                    }
                )
                send({"exit_code": 1})
            except OSError:
                pass
        finally:
            if process:
                process.close()
            with self.lock:
                self.clients.discard(client)
            client.close()
            self.capacity.release()
