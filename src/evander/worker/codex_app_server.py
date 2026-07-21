from __future__ import annotations

import json
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from queue import Empty, Queue
from typing import Any


class CodexAppServerError(RuntimeError):
    pass


class CodexAppServerTimeout(CodexAppServerError):
    pass


class CodexAppServerProcess:
    """Small synchronous JSONL client for ``codex app-server --stdio``.

    The process owns its stdin/stdout pair. Requests may be made concurrently;
    one reader thread routes responses, notifications, and server-initiated
    requests without ever logging protocol payloads (which may contain tokens).
    """

    def __init__(
        self,
        command: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        request_timeout_seconds: float = 30.0,
        server_request_handlers: Mapping[str, Callable[[dict[str, Any]], dict[str, Any] | None]]
        | None = None,
        notification_handler: Callable[[str, dict[str, Any]], None] | None = None,
        stderr_handler: Callable[[str], None] | None = None,
    ) -> None:
        if not command:
            raise ValueError("Codex app-server command must not be empty")
        self.command = tuple(str(part) for part in command)
        self.env = dict(env) if env is not None else None
        self.request_timeout_seconds = max(0.1, float(request_timeout_seconds))
        self.server_request_handlers = dict(server_request_handlers or {})
        self.notification_handler = notification_handler
        self.stderr_handler = stderr_handler
        self._process: subprocess.Popen[str] | None = None
        self._write_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._pending: dict[int, Queue[dict[str, Any]]] = {}
        self._next_request_id = 1
        self._closed = threading.Event()
        self._reader: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None

    @property
    def running(self) -> bool:
        process = self._process
        return process is not None and process.poll() is None and not self._closed.is_set()

    def start(self) -> None:
        if self.running:
            return
        self._closed.clear()
        try:
            process = subprocess.Popen(
                list(self.command),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=self.env,
            )
        except OSError as exc:
            raise CodexAppServerError(f"could not start Codex app-server: {exc}") from exc
        self._process = process
        self._reader = threading.Thread(
            target=self._read_stdout,
            name="evander-codex-app-server-stdout",
            daemon=True,
        )
        self._stderr_reader = threading.Thread(
            target=self._read_stderr,
            name="evander-codex-app-server-stderr",
            daemon=True,
        )
        self._reader.start()
        self._stderr_reader.start()
        try:
            self.request(
                "initialize",
                {
                    "clientInfo": {"name": "evander", "version": "1"},
                    "capabilities": {"experimentalApi": True},
                },
            )
            self.notify("initialized", {})
        except BaseException:
            try:
                self.close()
            finally:
                raise

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        if not self.running:
            if self._process is None:
                raise CodexAppServerError("Codex app-server is not running")
            raise CodexAppServerError(f"Codex app-server exited with status {self._process.poll()}")
        with self._state_lock:
            request_id = self._next_request_id
            self._next_request_id += 1
            response_queue: Queue[dict[str, Any]] = Queue(maxsize=1)
            self._pending[request_id] = response_queue
        self._write({"id": request_id, "method": method, "params": params or {}})
        timeout = (
            self.request_timeout_seconds
            if timeout_seconds is None
            else max(0.1, float(timeout_seconds))
        )
        try:
            response = response_queue.get(timeout=timeout)
        except Empty as exc:
            with self._state_lock:
                self._pending.pop(request_id, None)
            raise CodexAppServerTimeout(
                f"Codex app-server request {method!r} timed out after {timeout:g}s"
            ) from exc
        error = response.get("error")
        if error is not None:
            message = error.get("message") if isinstance(error, dict) else str(error)
            raise CodexAppServerError(
                f"Codex app-server request {method!r} failed: {message or 'unknown error'}"
            )
        result = response.get("result")
        if result is None:
            return {}
        if not isinstance(result, dict):
            raise CodexAppServerError(
                f"Codex app-server request {method!r} returned a non-object result"
            )
        return result

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        if not self.running:
            raise CodexAppServerError("Codex app-server is not running")
        self._write({"method": method, "params": params or {}})

    def respond(self, request_id: object, result: dict[str, Any] | None = None) -> None:
        self._write({"id": request_id, "result": result or {}})

    def respond_error(self, request_id: object, message: str) -> None:
        self._write({"id": request_id, "error": {"code": -32000, "message": message}})

    def close(self) -> None:
        self._closed.set()
        process = self._process
        if process is None:
            return
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        self._fail_pending("Codex app-server closed")

    def _write(self, payload: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None or process.poll() is not None:
            raise CodexAppServerError("Codex app-server is not running")
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        with self._write_lock:
            try:
                process.stdin.write(encoded + "\n")
                process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise CodexAppServerError("Codex app-server input stream closed") from exc

    def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        try:
            for line in process.stdout:
                if self._closed.is_set():
                    break
                text = line.strip()
                if not text:
                    continue
                try:
                    message = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if not isinstance(message, dict):
                    continue
                self._route_message(message)
        finally:
            self._closed.set()
            self._fail_pending("Codex app-server output stream closed")

    def _read_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        for line in process.stderr:
            if self._closed.is_set():
                break
            if self.stderr_handler is not None:
                self.stderr_handler(line.rstrip())

    def _route_message(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        request_id = message.get("id")
        if isinstance(method, str) and request_id is not None:
            self._handle_server_request(request_id, method, _dict_params(message.get("params")))
            return
        if isinstance(request_id, int):
            with self._state_lock:
                response_queue = self._pending.pop(request_id, None)
            if response_queue is not None:
                response_queue.put(message)
            return
        if isinstance(method, str) and self.notification_handler is not None:
            self.notification_handler(method, _dict_params(message.get("params")))

    def _handle_server_request(
        self, request_id: object, method: str, params: dict[str, Any]
    ) -> None:
        handler = self.server_request_handlers.get(method)
        if handler is None:
            self.respond_error(request_id, f"unsupported server request: {method}")
            return
        try:
            result = handler(params)
        except Exception as exc:  # noqa: BLE001 - protocol boundary must return an error.
            self.respond_error(request_id, str(exc) or exc.__class__.__name__)
            return
        self.respond(request_id, result)

    def _fail_pending(self, message: str) -> None:
        with self._state_lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for response_queue in pending:
            try:
                response_queue.put_nowait(
                    {"error": {"code": -32001, "message": message}, "failedAt": time.time()}
                )
            except Exception:
                pass


def _dict_params(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}
