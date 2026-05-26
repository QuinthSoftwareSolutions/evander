from __future__ import annotations

import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .base import EffectProviderBase

NODE_TYPE = "effect:runtime:diagnostics-snapshot"

DEFAULT_UNITS = (
    "agent-sandbox-admin-web.service",
    "agent-sandbox-trigger-orchestrator.service",
    "agent-sandbox-effect-orchestrator.service",
    "agent-sandbox-gateway.service",
    "agent-sandbox-worker-daemon.service",
    "agent-sandbox-ado-worker.service",
    "agent-sandbox-teams-ingest.service",
)
ALLOWED_UNITS = frozenset(DEFAULT_UNITS)
DEFAULT_SINCE = "24 hours ago"
DEFAULT_JOURNAL_LINES_PER_UNIT = 200
MAX_JOURNAL_LINES_PER_UNIT = 500
DEFAULT_RUNTIME_FILE_BYTES = 120_000
MAX_RUNTIME_FILE_BYTES = 262_144
DEFAULT_COMMAND_TIMEOUT_SECONDS = 10.0
MAX_COMMAND_TIMEOUT_SECONDS = 30.0

AUDIT_FILTER = re.compile(r'"decision"\s*:\s*"(?:error|denied)"|"error_class"')
EXECUTION_FAILURE_FILTER = re.compile(
    r'"success"\s*:\s*false|"status"\s*:\s*"(?:failed|error)"|"error"|'
    r'"exit_code"\s*:\s*[1-9]'
)

REDACTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"(?i)(Authorization\s*[:=]\s*)(Bearer|Basic|Token)\s+[A-Za-z0-9._~+/=-]+"),
        r"\1\2 <redacted>",
    ),
    (
        re.compile(
            r'(?i)("(?:authorization|proxy-authorization)"\s*:\s*)'
            r'"(?:Bearer|Basic|Token)\s+[^"]*"'
        ),
        r'\1"<redacted>"',
    ),
    (
        re.compile(
            r"(?i)((?:x-api-key|api-key|api_key|access_token|refresh_token|"
            r"client_secret|id_token|password|passwd|pwd|pat|token|secret|"
            r"apikey|connection_string|connstr)\s*[=:]\s*)"
            r"(\"[^\"]*\"|'[^']*'|[^,\s;}]+)"
        ),
        r"\1<redacted>",
    ),
    (
        re.compile(
            r'(?i)("(?:[^"]*(?:key|token|secret|password|passwd|pwd|pat|'
            r'connection_string|connstr)[^"]*)"\s*:\s*)("[^"]*"|null)'
        ),
        r'\1"<redacted>"',
    ),
    (re.compile(r"(?i)((?:Password|Pwd|User\s+Password)\s*=\s*)[^;,\s]+"), r"\1<redacted>"),
    (re.compile(r"(?i)(https?://)([^:/@\s]+):([^/@\s]+)@"), r"\1<redacted>:<redacted>@"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"), "sk-<redacted>"),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{20,}\b"), "github-token-<redacted>"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "github-pat-<redacted>"),
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
        "jwt-<redacted>",
    ),
    (
        re.compile(r'(?i)([?&](?:sig|signature|secrets?|tokens?|password|code)=)[^&\s"},]+'),
        r"\1<redacted>",
    ),
    (re.compile(r"(?i)(Set-Cookie\s*:\s*)[^\r\n]+"), r"\1<redacted>"),
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        "<redacted-private-key>",
    ),
)


@dataclass(frozen=True)
class _CommandResult:
    stdout: str
    stderr: str
    returncode: int | None
    error: str | None = None


class RuntimeDiagnosticsSnapshotEffectProvider(EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._definition_payload()
        if resolved_config:
            payload.update(dict(resolved_config))

        since = self._normalize_since(payload.get("since", DEFAULT_SINCE))
        units = self._normalize_units(payload.get("units"))
        include_failed_units = self._normalize_bool(payload.get("include_failed_units"), True)
        include_journal = self._normalize_bool(payload.get("include_journal"), True)
        include_runtime_files = self._normalize_bool(payload.get("include_runtime_files"), True)
        journal_lines = self._normalize_int(
            payload.get("max_journal_lines_per_unit"),
            default=DEFAULT_JOURNAL_LINES_PER_UNIT,
            minimum=1,
            maximum=MAX_JOURNAL_LINES_PER_UNIT,
            field_name="max_journal_lines_per_unit",
        )
        runtime_file_bytes = self._normalize_int(
            payload.get("max_runtime_file_bytes"),
            default=DEFAULT_RUNTIME_FILE_BYTES,
            minimum=1024,
            maximum=MAX_RUNTIME_FILE_BYTES,
            field_name="max_runtime_file_bytes",
        )
        command_timeout = self._normalize_float(
            payload.get("command_timeout_seconds"),
            default=DEFAULT_COMMAND_TIMEOUT_SECONDS,
            minimum=1.0,
            maximum=MAX_COMMAND_TIMEOUT_SECONDS,
            field_name="command_timeout_seconds",
        )

        runtime_dir = self._runtime_dir()
        project_dir = runtime_dir.parent if runtime_dir.name == "runtime" else runtime_dir
        sections: list[str] = []
        errors: list[str] = []
        lines = [
            "# Runtime diagnostics snapshot",
            f"generated_at={datetime.now(UTC).isoformat()}",
            f"project_dir={project_dir}",
            f"runtime_dir={runtime_dir}",
            f"since={since}",
            "redaction=enabled",
            "",
        ]

        if include_failed_units:
            sections.append("failed_systemd_units")
            lines.extend(self._failed_units_section(timeout_seconds=command_timeout, errors=errors))

        if include_journal:
            sections.append("service_journal")
            lines.extend(
                self._journal_section(
                    units=units,
                    since=since,
                    line_count=journal_lines,
                    timeout_seconds=command_timeout,
                    errors=errors,
                )
            )

        if include_runtime_files:
            sections.append("runtime_files")
            lines.extend(
                self._runtime_files_section(
                    runtime_dir=runtime_dir,
                    max_bytes=runtime_file_bytes,
                    errors=errors,
                )
            )

        raw_snapshot = "\n".join(lines).rstrip() + "\n"
        snapshot_text = self._redact(raw_snapshot)
        return {
            "success": True,
            "action": "runtime_diagnostics_snapshot",
            "message": snapshot_text,
            "stdout": snapshot_text,
            "stderr": "",
            "snapshot_text": snapshot_text,
            "runtime_dir": str(runtime_dir),
            "project_dir": str(project_dir),
            "since": since,
            "redaction": "enabled",
            "content_chars": len(snapshot_text),
            "sections": sections,
            "errors": errors,
            "units": list(units),
        }

    def _definition_payload(self) -> dict[str, Any]:
        definition = self.effect_definition
        payload: dict[str, Any] = {}
        if definition is None:
            return payload
        for field_name in (
            "since",
            "units",
            "include_failed_units",
            "include_journal",
            "include_runtime_files",
            "max_journal_lines_per_unit",
            "max_runtime_file_bytes",
            "command_timeout_seconds",
        ):
            if hasattr(definition, field_name):
                payload[field_name] = getattr(definition, field_name)
        return payload

    def _runtime_dir(self) -> Path:
        context = self.runtime_context
        if context is None:
            raise RuntimeError(f"{NODE_TYPE} has no runtime context")
        return context.config_path("runtime_dir")

    def _failed_units_section(self, *, timeout_seconds: float, errors: list[str]) -> list[str]:
        result = self._run_command(
            ["systemctl", "--no-pager", "--failed"],
            timeout_seconds=timeout_seconds,
        )
        return self._command_section(
            title="## Failed systemd units",
            result=result,
            errors=errors,
            error_prefix="systemctl --failed",
        )

    def _journal_section(
        self,
        *,
        units: tuple[str, ...],
        since: str,
        line_count: int,
        timeout_seconds: float,
        errors: list[str],
    ) -> list[str]:
        lines = ["## Selected service warnings and errors"]
        for unit in units:
            lines.append("")
            lines.append(f"### {unit}")
            result = self._run_command(
                [
                    "journalctl",
                    "--no-pager",
                    "-u",
                    unit,
                    "--since",
                    since,
                    "-p",
                    "warning..alert",
                    "-n",
                    str(line_count),
                ],
                timeout_seconds=timeout_seconds,
            )
            lines.extend(
                self._command_output_lines(
                    result=result,
                    errors=errors,
                    error_prefix=f"journalctl {unit}",
                )
            )
        lines.append("")
        return lines

    def _runtime_files_section(
        self, *, runtime_dir: Path, max_bytes: int, errors: list[str]
    ) -> list[str]:
        lines = ["## Runtime audit errors and denials"]
        audit_path = runtime_dir / "audit.jsonl"
        audit_text = self._read_tail(audit_path, max_bytes=max_bytes, errors=errors)
        if audit_text is None:
            lines.append(f"missing {audit_path}")
        else:
            filtered = self._filter_lines(audit_text, AUDIT_FILTER, limit=160)
            lines.extend(filtered or ["no matching audit errors or denials"])
        lines.append("")
        lines.append("## Recent orchestrator execution failures")
        for relative_path in (
            "effect-orchestrator-executions.json",
            "gate-orchestrator-executions.json",
            "worker-jobs.json",
        ):
            path = runtime_dir / relative_path
            lines.append("")
            lines.append(f"### {path}")
            text = self._read_tail(path, max_bytes=max_bytes, errors=errors)
            if text is None:
                lines.append("missing")
                continue
            filtered = self._filter_lines(text, EXECUTION_FAILURE_FILTER, limit=120)
            lines.extend(filtered or ["no matching failures"])
        lines.append("")
        return lines

    @staticmethod
    def _run_command(args: list[str], *, timeout_seconds: float) -> _CommandResult:
        try:
            completed = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            return _CommandResult("", "", None, error=f"command not found: {exc.filename}")
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout if isinstance(exc.stdout, str) else ""
            stderr = exc.stderr if isinstance(exc.stderr, str) else ""
            return _CommandResult(
                stdout,
                stderr,
                None,
                error=f"timed out after {timeout_seconds:g}s",
            )
        return _CommandResult(completed.stdout or "", completed.stderr or "", completed.returncode)

    @classmethod
    def _command_section(
        cls,
        *,
        title: str,
        result: _CommandResult,
        errors: list[str],
        error_prefix: str,
    ) -> list[str]:
        return [
            title,
            *cls._command_output_lines(result=result, errors=errors, error_prefix=error_prefix),
            "",
        ]

    @staticmethod
    def _command_output_lines(
        *,
        result: _CommandResult,
        errors: list[str],
        error_prefix: str,
    ) -> list[str]:
        lines: list[str] = []
        if result.error:
            message = f"{error_prefix}: {result.error}"
            errors.append(message)
            lines.append(message)
        output = "\n".join(part for part in (result.stdout, result.stderr) if part)
        if output.strip():
            lines.extend(output.rstrip().splitlines())
        if result.returncode not in (None, 0):
            message = f"{error_prefix}: exited with code {result.returncode}"
            errors.append(message)
            lines.append(message)
        if not lines:
            lines.append("no output")
        return lines

    @staticmethod
    def _read_tail(path: Path, *, max_bytes: int, errors: list[str]) -> str | None:
        try:
            with path.open("rb") as handle:
                handle.seek(0, 2)
                size = handle.tell()
                handle.seek(max(0, size - max_bytes))
                data = handle.read(max_bytes)
        except FileNotFoundError:
            return None
        except OSError as exc:
            errors.append(f"{path}: {exc}")
            return None
        return data.decode("utf-8", errors="replace")

    @staticmethod
    def _filter_lines(text: str, pattern: re.Pattern[str], *, limit: int) -> list[str]:
        lines = [line for line in text.splitlines() if pattern.search(line)]
        return lines[-limit:]

    @staticmethod
    def _normalize_since(value: Any) -> str:
        if value is None or value == "":
            return DEFAULT_SINCE
        since = str(value).strip()
        if not since:
            return DEFAULT_SINCE
        if "\n" in since or "\r" in since:
            raise ValueError(f"{NODE_TYPE} since must be a single-line value")
        if len(since) > 120:
            raise ValueError(f"{NODE_TYPE} since is too long")
        return since

    @staticmethod
    def _normalize_units(value: Any) -> tuple[str, ...]:
        if value is None or value == "":
            return DEFAULT_UNITS
        if isinstance(value, str):
            raw_units: Sequence[Any] = [value]
        elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
            raw_units = value
        else:
            raise ValueError(f"{NODE_TYPE} units must be a string list")
        units: list[str] = []
        for item in raw_units:
            unit = str(item).strip()
            if not unit:
                continue
            if unit not in ALLOWED_UNITS:
                raise ValueError(f"{NODE_TYPE} unit is not in the diagnostics allowlist: {unit}")
            if unit not in units:
                units.append(unit)
        return tuple(units)

    @staticmethod
    def _normalize_bool(value: Any, default: bool) -> bool:
        if value is None or value == "":
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
        raise ValueError(f"{NODE_TYPE} boolean fields must resolve to booleans")

    @staticmethod
    def _normalize_int(
        value: Any, *, default: int, minimum: int, maximum: int, field_name: str
    ) -> int:
        if value is None or value == "":
            return default
        if isinstance(value, bool):
            raise ValueError(f"{NODE_TYPE} {field_name} must be an integer")
        try:
            normalized = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{NODE_TYPE} {field_name} must be an integer") from exc
        if normalized < minimum or normalized > maximum:
            raise ValueError(f"{NODE_TYPE} {field_name} must be between {minimum} and {maximum}")
        return normalized

    @staticmethod
    def _normalize_float(
        value: Any, *, default: float, minimum: float, maximum: float, field_name: str
    ) -> float:
        if value is None or value == "":
            return default
        if isinstance(value, bool):
            raise ValueError(f"{NODE_TYPE} {field_name} must be a number")
        try:
            normalized = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{NODE_TYPE} {field_name} must be a number") from exc
        if normalized < minimum or normalized > maximum:
            raise ValueError(
                f"{NODE_TYPE} {field_name} must be between {minimum:g} and {maximum:g}"
            )
        return normalized

    @staticmethod
    def _redact(text: str) -> str:
        redacted = text
        for pattern, replacement in REDACTION_PATTERNS:
            redacted = pattern.sub(replacement, redacted)
        return redacted
