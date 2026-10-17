from __future__ import annotations

import logging
import os
import shutil
import stat
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from evander.codex_auth import (
    DEFAULT_SUBSCRIPTION_PROFILE_ID,
    CodexSubscriptionDeferral,
    CodexSubscriptionDeferredError,
    CodexTurnSpec,
)
from evander.fast_mode import fast_mode_service_tier

from .codex_app_server import CodexAppServerError, CodexAppServerProcess
from .codex_auth_broker import CodexAuthBroker, CodexExternalAuthTokens
from .model_usage import SubscriptionUsageRecorder

logger = logging.getLogger(__name__)

CODEX_COMPLETION_MARKER_CONTAINER_DIR = "/run/evander/codex-turns"
CODEX_COMPLETION_MARKER_HOST_DIRNAME = "codex-turns"


class CodexSubscriptionTurnRunner:
    """Drives one external-token Codex app-server turn inside a job container."""

    def __init__(
        self,
        *,
        command: Sequence[str],
        turn: CodexTurnSpec,
        cwd: str,
        broker: CodexAuthBroker,
        initial_tokens: CodexExternalAuthTokens,
        log_path: Path,
        finished_marker: Path,
        metadata_callback: Callable[[dict[str, Any]], None] | None = None,
        token_refresher: Callable[..., CodexExternalAuthTokens] | None = None,
        process_factory: Callable[..., CodexAppServerProcess] = CodexAppServerProcess,
        usage_recorder: SubscriptionUsageRecorder | None = None,
    ) -> None:
        self.command = tuple(command)
        self.turn = turn
        self.cwd = cwd
        self.broker = broker
        self._tokens = initial_tokens
        self._bound_account_id = initial_tokens.account_id
        self._token_refresher = token_refresher or broker.external_tokens
        self._sensitive_values = {initial_tokens.access_token}
        self.log_path = Path(log_path)
        self.finished_marker = Path(finished_marker)
        self.metadata_callback = metadata_callback
        self.usage_recorder = usage_recorder
        self._process_factory = process_factory
        self._completed = threading.Event()
        self._cancelled = threading.Event()
        self._turn_result: dict[str, Any] | None = None
        self._thread_id: str | None = None
        self._turn_id: str | None = None
        self._deferral: CodexSubscriptionDeferral | None = None
        self._startup_timings_ms: dict[str, float] = {}
        self._process: CodexAppServerProcess | None = None
        self._thread: threading.Thread | None = None
        self._log_lock = threading.Lock()
        self._metadata_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._thread is not None:
                raise RuntimeError("Codex subscription turn runner was already started")
            thread = threading.Thread(
                target=self.run,
                name="evander-codex-subscription-turn",
                daemon=True,
            )
            self._thread = thread
            try:
                thread.start()
            except Exception:
                self._thread = None
                raise

    def cancel(self) -> None:
        self._cancelled.set()
        with self._lifecycle_lock:
            process = self._process
            if process is not None:
                process.close()

    def join(self, *, timeout_seconds: float | None = None) -> bool:
        with self._lifecycle_lock:
            thread = self._thread
        if thread is None:
            return True
        if thread is threading.current_thread():
            return False
        thread.join(timeout=None if timeout_seconds is None else max(0.0, timeout_seconds))
        return not thread.is_alive()

    def cancel_and_wait(self, *, timeout_seconds: float = 40.0) -> None:
        self.cancel()
        if not self.join(timeout_seconds=timeout_seconds):
            raise RuntimeError("Codex subscription turn runner did not stop before cleanup")

    def run(self) -> None:
        exit_code = 1
        try:
            if self._cancelled.is_set():
                raise CodexAppServerError("Codex subscription turn was cancelled before startup")
            process = self._process_factory(
                self.command,
                server_request_handlers={
                    "account/chatgptAuthTokens/refresh": self._refresh_tokens,
                },
                notification_handler=self._handle_notification,
                stderr_handler=self._handle_stderr,
                request_timeout_seconds=30.0,
            )
            with self._lifecycle_lock:
                self._process = process
                if self._cancelled.is_set():
                    raise CodexAppServerError(
                        "Codex subscription turn was cancelled before startup"
                    )
                with self._startup_phase("process_start"):
                    process.start()
            if self._cancelled.is_set():
                raise CodexAppServerError("Codex subscription turn was cancelled")
            with self._startup_phase("account_login"):
                process.request("account/login/start", self._tokens.app_server_login_params())
            with self._startup_phase("thread_open"):
                self._thread_id = self._open_thread(process)
            self._append_log(f"session id: {self._thread_id}\n")
            if self.usage_recorder is not None:
                self.usage_recorder.expect_turn(self._thread_id)
            with self._startup_phase("turn_start"):
                turn_result = process.request(
                    "turn/start",
                    {
                        "threadId": self._thread_id,
                        "input": [{"type": "text", "text": self.turn.prompt}],
                        "cwd": self.cwd,
                        "model": self.turn.model,
                        "effort": self.turn.reasoning_effort or "high",
                        "serviceTier": fast_mode_service_tier(self.turn.fast_mode),
                        "approvalPolicy": "never",
                    },
                )
            turn_value = turn_result.get("turn")
            if isinstance(turn_value, dict):
                self._turn_id = str(turn_value.get("id") or "").strip() or None
            self._publish_metadata()
            while not self._cancelled.is_set():
                if self._completed.wait(timeout=0.5):
                    break
                if not process.running:
                    raise CodexAppServerError(
                        "Codex app-server exited before reporting turn completion"
                    )
            if self._cancelled.is_set():
                raise CodexAppServerError("Codex subscription turn was cancelled")
            turn = self._turn_result or {}
            status = str(turn.get("status") or "").strip().casefold()
            error = turn.get("error")
            if status == "completed":
                exit_code = 0
            else:
                message = (
                    _turn_error_message(error) or f"turn ended with status {status or 'unknown'}"
                )
                self._deferral = _turn_capacity_deferral(
                    error,
                    profile_id=(
                        self.turn.subscription_profile_id or DEFAULT_SUBSCRIPTION_PROFILE_ID
                    ),
                )
                self._append_log(f"codex error: {message}\n")
        except Exception as exc:  # noqa: BLE001 - background job boundary.
            if not self._cancelled.is_set():
                if self._deferral is None:
                    self._deferral = _turn_capacity_deferral(
                        str(exc),
                        profile_id=(
                            self.turn.subscription_profile_id or DEFAULT_SUBSCRIPTION_PROFILE_ID
                        ),
                    )
                self._append_log(
                    f"codex error: {self._redact(str(exc) or exc.__class__.__name__)}\n"
                )
            logger.warning(
                "codex_subscription_turn_failed exception_class=%s exception_message=%s",
                exc.__class__.__name__,
                self._redact(str(exc)),
            )
        finally:
            with self._lifecycle_lock:
                process = self._process
                if process is not None:
                    process.close()
                    self._process = None
            if self.usage_recorder is not None:
                self.usage_recorder.finish(status="success" if exit_code == 0 else "failed")
            # Persist the structured outcome before releasing the container's
            # completion wait. Otherwise a fast status poll can observe the
            # terminal exit without the deferral needed for durable replay.
            try:
                self._publish_metadata(exit_code=exit_code)
            finally:
                try:
                    write_completion_marker_no_follow(self.finished_marker, exit_code)
                except OSError as exc:
                    self._append_log(
                        "codex error: could not write the protected completion marker "
                        f"({exc.__class__.__name__})\n"
                    )
                    logger.error(
                        "codex_subscription_completion_marker_failed exception_class=%s",
                        exc.__class__.__name__,
                    )

    @contextmanager
    def _startup_phase(self, phase: str) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            self._startup_timings_ms[phase] = elapsed_ms
            logger.info(
                "codex_subscription_startup phase=%s duration_ms=%.3f",
                phase,
                elapsed_ms,
            )

    def _open_thread(self, process: CodexAppServerProcess) -> str:
        thread_id = self.turn.resume_thread_id
        if not thread_id and self.turn.resume_mode == "same_context":
            listed = process.request(
                "thread/list",
                {
                    "cwd": self.cwd,
                    "limit": 1,
                    "modelProviders": ["openai", "evander_openai_relay"],
                    "sortKey": "updated_at",
                    "sortDirection": "desc",
                    "sourceKinds": [],
                },
            )
            data = listed.get("data")
            first = data[0] if isinstance(data, list) and data else None
            if isinstance(first, dict):
                thread_id = str(first.get("id") or "").strip() or None
        common: dict[str, Any] = {
            "cwd": self.cwd,
            "model": self.turn.model,
            "modelProvider": "openai",
            "approvalPolicy": "never",
            # Docker is the isolation boundary for worker jobs. Match metered Codex
            # turns so managed Git/package access and the /artifacts mount remain usable.
            "sandbox": "danger-full-access",
        }
        config: dict[str, Any] = {"model_reasoning_effort": self.turn.reasoning_effort or "high"}
        config["service_tier"] = fast_mode_service_tier(self.turn.fast_mode)
        if self.turn.fast_mode:
            config["features"] = {"fast_mode": True}
        if (
            self.turn.reasoning_effort is not None
            and self.turn.reasoning_effort_source != "default"
        ):
            config["model_supports_reasoning_summaries"] = True
        if self.turn.mcp_gateway_url is not None:
            config["mcp_servers"] = {"evander": {"url": str(self.turn.mcp_gateway_url)}}
        config.setdefault("features", {})["web_search_request"] = self.turn.web_search_enabled
        common["config"] = config
        if thread_id:
            result = process.request("thread/resume", {"threadId": thread_id, **common})
        else:
            result = process.request("thread/start", common)
        thread = result.get("thread")
        opened_id = str(thread.get("id") or "").strip() if isinstance(thread, dict) else ""
        if not opened_id:
            raise CodexAppServerError("Codex did not return a thread id")
        return opened_id

    def _refresh_tokens(self, params: dict[str, Any]) -> dict[str, Any]:
        previous_account_id = str(params.get("previousAccountId") or "").strip() or None
        try:
            tokens = self._token_refresher(
                previous_access_token=self._tokens.access_token,
                previous_account_id=previous_account_id,
            )
            if tokens.account_id != self._bound_account_id:
                raise CodexSubscriptionDeferredError(
                    CodexSubscriptionDeferral(
                        profile_id=(
                            self.turn.subscription_profile_id or DEFAULT_SUBSCRIPTION_PROFILE_ID
                        ),
                        reason="unavailable",
                    ),
                    "Codex subscription account changed during the running turn",
                )
        except CodexSubscriptionDeferredError as exc:
            self._deferral = exc.deferral
            raise
        self._tokens = tokens
        self._sensitive_values.add(self._tokens.access_token)
        return self._tokens.refresh_response()

    def _handle_notification(self, method: str, params: dict[str, Any]) -> None:
        if self.usage_recorder is not None:
            self.usage_recorder.notification(method, params)
        if method == "item/completed":
            item = params.get("item")
            if isinstance(item, dict) and item.get("type") == "agentMessage":
                text = str(item.get("text") or "").strip()
                if text:
                    self._append_log(text + "\n")
            return
        if method == "turn/completed":
            thread_id = str(params.get("threadId") or "").strip()
            turn = params.get("turn")
            if self._thread_id and thread_id and thread_id != self._thread_id:
                return
            if isinstance(turn, dict):
                turn_id = str(turn.get("id") or "").strip()
                if self._turn_id and turn_id and turn_id != self._turn_id:
                    return
                self._turn_result = dict(turn)
                self._completed.set()
            return
        if method == "error":
            message = str(params.get("message") or "Codex app-server error").strip()
            self._append_log(f"codex error: {message}\n")

    def _handle_stderr(self, line: str) -> None:
        text = str(line or "").strip()
        if text:
            self._append_log(f"codex app-server: {self._redact(text)}\n")

    def _append_log(self, text: str) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._log_lock:
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(text)
                stream.flush()

    def _redact(self, text: str) -> str:
        redacted = text
        for value in self._sensitive_values:
            if value:
                redacted = redacted.replace(value, "[REDACTED]")
        return redacted

    def _publish_metadata(self, *, exit_code: int | None = None) -> None:
        if self.metadata_callback is None:
            return
        metadata: dict[str, Any] = {
            "codex_auth_mode": "chatgpt_subscription",
            "codex_subscription_startup_ms": dict(self._startup_timings_ms),
            "subscription_profile_id": (
                self.turn.subscription_profile_id or DEFAULT_SUBSCRIPTION_PROFILE_ID
            ),
        }
        if self._deferral is not None:
            metadata["codex_subscription_deferral"] = self._deferral.model_dump(
                mode="json", exclude_none=True
            )
        if self._thread_id:
            metadata["codex_subscription_thread_id"] = self._thread_id
        if self._turn_id:
            metadata["codex_subscription_turn_id"] = self._turn_id
        if exit_code is not None:
            metadata["codex_subscription_exit_code"] = exit_code
        with self._metadata_lock:
            self.metadata_callback(metadata)


def _turn_error_message(error: object) -> str | None:
    if isinstance(error, dict):
        message = str(error.get("message") or "").strip()
        if message:
            return message
    if error is not None:
        text = str(error).strip()
        return text or None
    return None


def _turn_capacity_deferral(
    error: object,
    *,
    profile_id: str,
) -> CodexSubscriptionDeferral | None:
    """Recognize App Server subscription exhaustion without relying on one error shape."""

    if not _is_subscription_capacity_error(error):
        return None
    retry_at = _find_reset_timestamp(error)
    return CodexSubscriptionDeferral(
        profile_id=profile_id,
        reason="rate_limited",
        retry_at=retry_at,
    )


_SUBSCRIPTION_CAPACITY_CODES = {
    "insufficientquota",
    "ratelimit",
    "ratelimitexceeded",
    "ratelimitreached",
    "usagelimit",
    "usagelimitexceeded",
    "usagelimitreached",
    "workspaceownercreditsdepleted",
    "workspacemembercreditsdepleted",
    "workspaceownerusagelimitreached",
    "workspacememberusagelimitreached",
}
_SUBSCRIPTION_CAPACITY_CODE_KEYS = {
    "code",
    "codexerrorinfo",
    "errorcode",
    "errortype",
    "ratelimitreachedtype",
    "reason",
    "type",
}
_SUBSCRIPTION_CAPACITY_MESSAGE_MARKERS = (
    "rate limit reached",
    "usage limit reached",
    "usage rate limit",
    "hit your usage limit",
    "chatgpt rate limit",
    "chatgpt quota",
    "subscription rate limit",
    "subscription quota",
)


def _is_subscription_capacity_error(value: object) -> bool:
    if isinstance(value, dict):
        for key, candidate in value.items():
            normalized_key = _normalized_error_code(key)
            if normalized_key in _SUBSCRIPTION_CAPACITY_CODE_KEYS:
                if _normalized_error_code(candidate) in _SUBSCRIPTION_CAPACITY_CODES:
                    return True
            if normalized_key in {"message", "additionaldetails"} and _capacity_message(candidate):
                return True
            if isinstance(candidate, dict | list) and _is_subscription_capacity_error(candidate):
                return True
        return False
    if isinstance(value, list):
        return any(_is_subscription_capacity_error(candidate) for candidate in value)
    return _capacity_message(value)


def _normalized_error_code(value: object) -> str:
    return "".join(character for character in str(value).casefold() if character.isalnum())


def _capacity_message(value: object) -> bool:
    message = str(value or "").strip().casefold()
    return any(marker in message for marker in _SUBSCRIPTION_CAPACITY_MESSAGE_MARKERS)


def _find_reset_timestamp(value: object) -> object | None:
    if isinstance(value, dict):
        for key, candidate in value.items():
            normalized = str(key).replace("_", "").casefold()
            if normalized in {"resetsat", "retryat", "resetat"}:
                parsed = _parse_reset_timestamp(candidate)
                if parsed is not None:
                    return parsed
        for candidate in value.values():
            parsed = _find_reset_timestamp(candidate)
            if parsed is not None:
                return parsed
    elif isinstance(value, list):
        for candidate in value:
            parsed = _find_reset_timestamp(candidate)
            if parsed is not None:
                return parsed
    return None


def _parse_reset_timestamp(value: object) -> object | None:
    from datetime import UTC, datetime

    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=UTC)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def write_completion_marker_no_follow(path: Path, exit_code: int) -> None:
    """Create a fresh regular marker without following an agent-created link."""

    marker = Path(path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    last_error: OSError | None = None
    displaced_entries: list[Path] = []
    try:
        for _attempt in range(3):
            try:
                descriptor = os.open(marker, flags, 0o600)
            except FileExistsError as exc:
                last_error = exc
                # Atomically move any agent-created entry aside. Unlike unlink(), rename
                # also handles non-empty directories and does not follow symlinks.
                displaced = marker.with_name(f".{marker.name}.stale-{uuid4().hex}")
                try:
                    os.replace(marker, displaced)
                except FileNotFoundError:
                    continue
                displaced_entries.append(displaced)
                continue
            try:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise OSError("completion marker is not a regular file")
                payload = str(int(exit_code)).encode("ascii")
                if os.write(descriptor, payload) != len(payload):
                    raise OSError("completion marker write was incomplete")
                os.fsync(descriptor)
                return
            finally:
                os.close(descriptor)
        raise last_error or OSError("could not create completion marker without following links")
    finally:
        for displaced in displaced_entries:
            _remove_displaced_completion_marker(displaced)


def _remove_displaced_completion_marker(path: Path) -> None:
    """Best-effort cleanup after an obstruction has been moved out of the marker path."""

    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return
    try:
        if stat.S_ISDIR(mode):
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    except OSError as exc:
        # Marker delivery has already been unblocked. A hostile directory's permissions
        # must not turn cleanup of the displaced entry into a daemon-startup failure.
        logger.warning(
            "codex_subscription_displaced_marker_cleanup_failed exception_class=%s",
            exc.__class__.__name__,
        )
