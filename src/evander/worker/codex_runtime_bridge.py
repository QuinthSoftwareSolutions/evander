"""Job-scoped Codex transport. Subscription credentials remain on the worker.

The socket is mounted exclusively into its owning container. It neither exposes
tokens nor accepts host commands, profiles, image names or container selectors.
"""

from __future__ import annotations

import json
import math
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
from .codex_runtime_http import CodexRuntimeHttpRelay, placeholder_tokens
from .codex_runtime_policy import RUNTIME_PROVIDER_ID, verify_runtime_requirements
from .unix_socket import bind_unix_socket


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
                merge_config(config, scan_config(parsed))
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
    if mode == "exec" and prompt is None:
        raise ValueError("Brokered exec requires an explicit prompt")
    return mode, cwd, config, model, prompt


def merge_config(target, source):
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            merge_config(target[key], value)
        else:
            target[key] = value


def scan_config(config):
    """Accept the pinned scanner's options, never arbitrary native settings."""
    config = dict(config)
    for key, allowed in {
        "model_provider": ("openai",),
        "model_providers": ({},),
        "forced_login_method": ("chatgpt",),
        "cli_auth_credentials_store": ("auto", "ephemeral"),
    }.items():
        if key in config:
            if config.pop(key) not in allowed:
                raise ValueError("Subscription authentication is owned by the worker")
    allowed = {
        "model",
        "model_reasoning_effort",
        "model_reasoning_summary",
        "model_verbosity",
        "approval_policy",
        "approvals_reviewer",
        "default_permissions",
        "permissions",
        "mcp_servers",
        "features",
        "agents",
        "show_raw_agent_reasoning",
        "windows",
        "allow_login_shell",
    }
    if set(config) - allowed:
        raise ValueError("Unsupported brokered Codex configuration")
    for key, fields in {
        "features": {"plugins", "goals", "multi_agent_v2", "enable_fanout"},
        "agents": {"max_threads"},
        "windows": {"sandbox"},
    }.items():
        if key in config and (not isinstance(config[key], dict) or set(config[key]) - fields):
            raise ValueError("Unsupported brokered Codex configuration")
    # Re-encode parsed values instead of forwarding strings with a potentially
    # different interpretation in the native CLI parser.
    for value in config.values():
        toml_value(value)
    return config


def toml_value(value):
    if isinstance(value, dict):
        return "{" + ",".join(f"{json.dumps(k)}={toml_value(v)}" for k, v in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ",".join(toml_value(item) for item in value) + "]"
    if isinstance(value, (str, bool, int)) or isinstance(value, float) and math.isfinite(value):
        return json.dumps(value)
    raise ValueError("Unsupported Codex configuration value")


def preflight_request(method, params):
    """Raw app-server mode exists only for the SDK's read-only preflight."""
    fields = {
        "initialize": {"clientInfo", "capabilities"},
        "initialized": set(),
        "config/read": {"cwd", "includeLayers"},
        "permissionProfile/list": {"cwd", "cursor", "limit"},
        "configRequirements/read": set(),
        "account/read": {"refreshToken"},
    }
    if method not in fields or not isinstance(params, dict) or set(params) - fields[method]:
        raise ValueError("Unsupported Codex preflight request")
    if params.get("cwd") is not None:
        if not isinstance(params["cwd"], str):
            raise ValueError("Invalid preflight working directory")
        allowed_path(params["cwd"])
    if method == "account/read" and params.get("refreshToken", False) is not False:
        raise ValueError("Runtime clients cannot refresh authentication")
    return params


class CodexRuntimeBridge:
    def __init__(
        self,
        *,
        path: Path,
        command_factory,
        tokens,
        refresh_tokens,
        http_client,
        max_processes=12,
        process_factory=CodexAppServerProcess,
        diagnostics=None,
    ):
        self.path = path
        self.command_factory = command_factory
        self.diagnostics = diagnostics
        self.native_tokens = placeholder_tokens(tokens)
        self.http_relay = CodexRuntimeHttpRelay(
            path=path.with_name("models.sock"),
            tokens=tokens,
            refresh_tokens=refresh_tokens,
            client=http_client,
            max_connections=max_processes,
            diagnostics=diagnostics,
        )
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
        bind_unix_socket(self.listener, self.path)
        self.path.chmod(0o666)  # Reachable only through the job's private bind mount.
        self.listener.listen(16)
        self.listener.settimeout(0.5)
        try:
            self.http_relay.start()
        except BaseException:
            self.close()
            raise
        threading.Thread(target=self._accept, name="codex-runtime-broker", daemon=True).start()

    def close(self):
        self.closed.set()
        self.http_relay.close()
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
        exit_code = None
        send_lock = threading.Lock()
        connection_id = os.urandom(8).hex()

        def record(event, **data):
            if self.diagnostics is not None:
                try:
                    self.diagnostics.event(event, {"connection_id": connection_id, **data})
                except Exception:
                    pass  # Evidence collection must not interrupt execution.

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
                    [*command[: command.index("app-server")], "--version"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=True,
                )
                record("runtime.version", version=result.stdout[:2000])
                send({"stream": "stdout", "text": result.stdout})
                exit_code = 0
                return
            mode, cwd, config, model, prompt = parse_arguments(argv, cwd)
            record(
                "runtime.started",
                mode=mode,
                cwd=cwd,
                model=model,
                reasoning_effort=config.get("model_reasoning_effort"),
                prompt=prompt,
            )
            overrides = [
                f"{key}={toml_value(value)}"
                for key, value in config.items()
                if not key.startswith("_")
            ]
            done = threading.Event()
            terminal = {}
            usage = {}
            thread_id = None

            def notification(method, params):
                # Observe children before exec's parent-thread projection. Completed
                # items contain the full text, so omit high-volume streaming deltas.
                # Authentication messages are never diagnostic events.
                if not method.endswith("/delta") and (
                    not method.startswith("account/") or method == "account/rateLimits/updated"
                ):
                    record("native.notification", method=method, params=params)
                if mode == "app-server":
                    send(
                        {
                            "stream": "stdout",
                            "text": json.dumps({"method": method, "params": params}) + "\n",
                        }
                    )
                    return
                # Native subagents share this app-server connection. Their messages,
                # usage and completion belong to their own thread, not this exec.
                if params.get("threadId") not in {None, thread_id}:
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

            def refresh(params):
                if params.get("previousAccountId") not in {None, self.native_tokens.account_id}:
                    raise ValueError("Subscription account changed during runtime execution")
                return self.native_tokens.refresh_response()

            process = self.process_factory(
                self.command_factory(env, overrides),
                server_request_handlers={"account/chatgptAuthTokens/refresh": refresh},
                notification_handler=notification,
                stderr_handler=lambda line: record("native.stderr", line=line),
                drain_stderr_on_close=self.diagnostics is not None,
            )
            process.start()
            verify_runtime_requirements(process.request("configRequirements/read", {}))
            process.request("account/login/start", self.native_tokens.app_server_login_params())
            if mode == "app-server":
                while line := reader.readline(16 * 1024 * 1024 + 1):
                    if len(line) > 16 * 1024 * 1024:
                        raise ValueError("Codex frame exceeds size limit")
                    message = json.loads(line)
                    method = message.get("method", "")
                    params = preflight_request(method, message.get("params") or {})
                    if method == "initialize":
                        result = process.initialization_result
                    elif method == "initialized":
                        continue
                    elif "id" not in message:
                        raise ValueError("Expected a preflight request identity")
                    else:
                        result = process.request(method, params)
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
                        "modelProvider": RUNTIME_PROVIDER_ID,
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
                record(
                    "runtime.thread",
                    thread_id=thread_id,
                    model=thread.get("model"),
                    reasoning_effort=thread.get("reasoningEffort"),
                )
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
                        record("runtime.disconnected", thread_id=thread_id)
                        return
                ok = terminal.get("status") == "completed"
                record("runtime.completed", thread_id=thread_id, turn=terminal, usage=usage)
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
                exit_code = 0 if ok else 1
        except Exception as exc:
            record("runtime.failed", exception_class=type(exc).__name__)
            # Never return native exception text: native auth failures may contain tokens.
            try:
                send(
                    {
                        "stream": "stderr",
                        "text": f"Managed Codex runtime failed ({type(exc).__name__})\n",
                    }
                )
                exit_code = 1
            except OSError:
                pass
        finally:
            native_was_running = bool(process and process.running)
            if process:
                process.close()
            record(
                "runtime.closed",
                broker_shutdown=self.closed.is_set(),
                native_was_running=native_was_running,
                native_exit_code=getattr(process, "returncode", None),
            )
            # The runner snapshots native history upon receiving this receipt.
            # Close/reap the native writer first so final records are flushed.
            if exit_code is not None:
                try:
                    send({"exit_code": exit_code})
                except OSError:
                    pass
            with self.lock:
                self.clients.discard(client)
            client.close()
            self.capacity.release()
