"""Job-scoped Codex transport. Subscription credentials remain on the worker.

The socket is mounted exclusively into its owning container. It neither exposes
tokens nor accepts host commands, profiles, image names or container selectors.
"""

from __future__ import annotations

import json
import math
import os
import re
from datetime import datetime
from pathlib import Path, PurePosixPath
import select
import socket
import subprocess
import threading
import time
import tomllib
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from evander.cyber_access import CyberAccessProgram, CYBER_ACCESS_PROGRAM_RPC

from .codex_app_server import CodexAppServerProcess
from .codex_runtime_http import CodexRuntimeHttpRelay, placeholder_tokens
from .codex_runtime_policy import RUNTIME_PROVIDER_ID, verify_runtime_requirements
from .unix_socket import bind_unix_socket


class CodexRuntimeProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command_prefix: list[str] = Field(min_length=1)
    codex_path: str = "/usr/local/bin/codex"
    max_processes: int = Field(default=12, ge=1, le=64)
    default_cyber_access_program: CyberAccessProgram | None = None
    sandbox_policy: Literal["native", "container"] = "native"


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
    if not isinstance(env, dict) or set(env) - allowed:
        raise ValueError("Unsupported Codex runtime environment")
    for key, value in env.items():
        if not isinstance(value, str) or "\x00" in value:
            raise ValueError("Invalid runtime environment value")
        if key in {"CODEX_SECURITY_SURFACE", "CODEX_SECURITY_TARGET_DISPLAY_NAME"}:
            if not value or len(value) > 255 or any(ord(c) < 32 for c in value):
                raise ValueError("Invalid SDK display context")
        elif key == "CODEX_SECURITY_STARTED_AT":
            if datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is None:
                raise ValueError("SDK start time must include a timezone")
        elif key in {
            "CODEX_SECURITY_SCAN_ID",
            "CODEX_SECURITY_TARGET_ID",
            "CODEX_SECURITY_TARGET_REVISION",
            "CODEX_SECURITY_TARGET_SNAPSHOT_DIGEST",
        }:
            patterns = {
                "CODEX_SECURITY_SCAN_ID": r"(?:[a-f0-9]{32}|[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12})",
                "CODEX_SECURITY_TARGET_ID": r"target_sha256_[a-f0-9]{64}",
                "CODEX_SECURITY_TARGET_REVISION": r"(?:[a-f0-9]{40}|[a-f0-9]{64})",
                "CODEX_SECURITY_TARGET_SNAPSHOT_DIGEST": r"codex-security-snapshot/v1:sha256:[a-f0-9]{64}",
            }
            if not re.fullmatch(patterns[key], value):
                raise ValueError("Invalid SDK scan identity")
        elif key == "CODEX_SECURITY_TARGET_KIND":
            if value not in {"git_revision", "git_worktree", "git_diff", "directory_snapshot"}:
                raise ValueError("Invalid SDK target kind")
        else:
            allowed_path(
                value, executable=key in {"CODEX_CLI_PATH", "PYTHON", "CODEX_SECURITY_PLUGIN_ROOT"}
            )
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
                if value not in CYBER_ACCESS_PROGRAM_RPC:
                    raise ValueError("Unsupported Cyber access program")
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
        "responses_api_metadata",
    }
    if set(config) - allowed:
        raise ValueError("Unsupported brokered Codex configuration")
    if "responses_api_metadata" in config:
        metadata = config["responses_api_metadata"]
        if (
            not isinstance(metadata, dict)
            or set(metadata) != {"codex_security_surface"}
            or metadata["codex_security_surface"] not in {"cli", "sdk"}
        ):
            raise ValueError("Unsupported SDK request metadata")
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


def public_runtime_failure(error):
    """Expose a retry category while keeping native error details private."""
    value = json.dumps(error, default=str)
    if re.search(r"quota|usage.limit|rate.limit|\b429\b", value, re.I):
        return "Managed Codex subscription quota is unavailable"
    if re.search(r"unauthorized|authentication|\b401\b", value, re.I):
        return "Managed Codex authentication failed"
    if re.search(r"cyberPolicy|cybersecurity risk|forbidden|\b403\b", value, re.I):
        return "Managed Codex policy denied the request"
    return "Brokered Codex turn failed"


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
        on_drained=None,
        usage_recorder_factory=None,
        default_cyber_access_program=None,
        sandbox_policy="native",
    ):
        if sandbox_policy not in {"native", "container"}:
            raise ValueError("Unsupported runtime sandbox policy")
        self.path = path
        self.command_factory = command_factory
        self.diagnostics = diagnostics
        self.on_drained = on_drained
        self.usage_recorder_factory = usage_recorder_factory
        self.default_cyber_access_program = default_cyber_access_program
        self.sandbox_policy = sandbox_policy
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
        self.drain = threading.Condition(self.lock)
        self.shutdown_ready = False
        self.drain_notified = False
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

    def diagnostic(self, event, **data):
        if self.diagnostics is not None:
            try:
                self.diagnostics.event(event, data)
            except Exception:
                pass  # Evidence collection must not interrupt execution.

    def wait_closed(self, *, timeout_seconds=0):
        with self.drain:
            return self.drain.wait_for(
                lambda: self.shutdown_ready and not self.clients,
                timeout=max(0, timeout_seconds),
            )

    def _notify_drained(self):
        with self.drain:
            self.drain.notify_all()
            if not self.shutdown_ready or self.clients or self.drain_notified:
                return
            self.drain_notified = True
        if self.on_drained is not None:
            try:
                self.on_drained()
            except Exception as exc:
                self.diagnostic("runtime.finalization_failed", exception_class=type(exc).__name__)

    def close(self, *, timeout_seconds=15):
        deadline = time.monotonic() + max(0, timeout_seconds)
        with self.drain:
            first = not self.closed.is_set()
            self.closed.set()
            clients = list(self.clients)
        if first:
            try:
                self.http_relay.close()
            except Exception as exc:
                self.diagnostic("runtime.shutdown_failed", exception_class=type(exc).__name__)
            if self.listener:
                self.listener.close()
            for client in clients:
                try:
                    client.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            try:
                self.path.unlink(missing_ok=True)
            finally:
                with self.drain:
                    self.shutdown_ready = True
                self._notify_drained()
        drained = self.wait_closed(timeout_seconds=max(0, deadline - time.monotonic()))
        if not drained:
            with self.drain:
                remaining = len(self.clients)
            self.diagnostic("runtime.shutdown_timeout", pending_connections=remaining)
        return drained

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
                if self.closed.is_set():
                    client.close()
                    self.capacity.release()
                    return
                self.clients.add(client)
            threading.Thread(target=self._serve, args=(client,), daemon=True).start()

    def _serve(self, client):
        process = None
        usage_recorder = None
        exit_code = None
        send_lock = threading.Lock()
        connection_id = os.urandom(8).hex()

        def record(event, **data):
            self.diagnostic(event, connection_id=connection_id, **data)

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
            if argv == ["login", "status"]:
                # SDK authentication discovery may inspect status, but never
                # log in, log out, import a token, or select another profile.
                send(
                    {
                        "stream": "stdout",
                        "text": "Logged in using ChatGPT (managed worker subscription)\n",
                    }
                )
                exit_code = 0
                return
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
            if self.sandbox_policy == "container":
                # This opt-in comes only from the worker-owned runtime profile.
                # Docker still enforces the job's mounts, UID, capabilities and
                # egress. Native nested namespaces are unavailable on some workers.
                config["default_permissions"] = "evander_worker_container"
                config["permissions"] = {
                    "evander_worker_container": {
                        "filesystem": {":root": "write"},
                        "network": {"enabled": True},
                    }
                }
            if self.usage_recorder_factory is not None:
                usage_recorder = self.usage_recorder_factory(model)
            record(
                "runtime.started",
                mode=mode,
                cwd=cwd,
                model=model,
                reasoning_effort=config.get("model_reasoning_effort"),
                sandbox_policy=self.sandbox_policy,
                requested_cyber_access_program=config.get(
                    "_cyber_access_program", self.default_cyber_access_program
                ),
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
                if usage_recorder is not None:
                    usage_recorder.notification(method, params)
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

            if self.closed.is_set():
                raise RuntimeError("Codex runtime is closing")
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
                cyber_access_program = config.pop(
                    "_cyber_access_program", self.default_cyber_access_program
                )
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
                if usage_recorder is not None:
                    usage_recorder.expect_turn(thread_id)
                process.request(
                    "turn/start",
                    {
                        "threadId": thread_id,
                        "cwd": cwd,
                        "approvalPolicy": "never",
                        "input": [{"type": "text", "text": prompt}],
                        **(
                            {"cyberAccessProgram": CYBER_ACCESS_PROGRAM_RPC[cyber_access_program]}
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
                    else {
                        "type": "turn.failed",
                        "error": {"message": public_runtime_failure(terminal.get("error"))},
                    }
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
            try:
                if process:
                    process.close()
            except Exception as exc:
                record("runtime.close_failed", exception_class=type(exc).__name__)
                exit_code = 1
            if usage_recorder is not None:
                usage_recorder.finish(status="success" if exit_code == 0 else "cancelled")
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
            client.close()
            self.capacity.release()
            with self.drain:
                self.clients.discard(client)
            self._notify_drained()
