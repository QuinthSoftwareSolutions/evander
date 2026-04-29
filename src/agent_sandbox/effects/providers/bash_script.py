from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping, Sequence
from typing import Any

from agent_sandbox.events.models import EffectDefinitionDiscriminated, WorkflowNodeEnvelope
from agent_sandbox.events.provider_models import BashScriptEffectDefinition

from .base import EffectProviderBase
from .bash_script_schema import BashScriptEffectConfigSchema


class BashScriptEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(
        cls, effect_definition: EffectDefinitionDiscriminated
    ) -> EffectDefinitionDiscriminated:
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            config = BashScriptEffectConfigSchema.model_validate(
                effect_definition.config
            ).model_dump()
            return BashScriptEffectDefinition.model_validate(
                {
                    "id": effect_definition.id,
                    "display_name": effect_definition.display_name,
                    "type": effect_definition.type,
                    "outputs": effect_definition.outputs,
                    **config,
                }
            )
        return super().validate_definition(effect_definition)

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(resolved_config or {})
        script_path = self._normalize_required_string(
            payload.get("script_path"), field_name="script_path"
        )
        args = self._normalize_args(payload.get("args"))
        working_directory = self._normalize_optional_string(payload.get("working_directory"))
        stdin = self._normalize_optional_string(payload.get("stdin"))
        environment = self._normalize_environment(payload.get("environment"))
        timeout_seconds = self._as_float_or_none(payload.get("timeout_seconds"))
        check = bool(payload.get("check", True))

        command = ["bash", script_path, *args]
        env = None
        if environment:
            env = dict(self._context().env)
            env.update(environment)

        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            cwd=working_directory,
            input=stdin,
            env=env,
            timeout=timeout_seconds,
            check=False,
        )
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        success = completed.returncode == 0
        message = (
            stderr.strip() or stdout.strip() or f"Script exited with code {completed.returncode}"
        )
        result = {
            "success": success,
            "action": "bash_script",
            "script_path": script_path,
            "message": message,
            "exit_code": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
        }
        parsed_output = self._parse_stdout_json_object(stdout)
        if parsed_output:
            result["parsed_output"] = parsed_output
            for key, value in parsed_output.items():
                result.setdefault(key, value)
        if check and not success:
            raise RuntimeError(message)
        return result

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("BashScriptEffectProvider has no runtime context")
        return self.runtime_context

    @staticmethod
    def _normalize_optional_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @classmethod
    def _normalize_required_string(cls, value: Any, *, field_name: str) -> str:
        text = cls._normalize_optional_string(value)
        if text is None:
            raise ValueError(f"effect:bash:script requires a non-empty {field_name}")
        return text

    @classmethod
    def _normalize_args(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            raise ValueError("effect:bash:script args must be a sequence of values")
        args: list[str] = []
        for item in value:
            if item is None:
                args.append("")
                continue
            if isinstance(item, (Mapping, list, tuple, set)):
                raise ValueError("effect:bash:script args entries must be scalar values")
            args.append(str(item))
        return args

    @staticmethod
    def _normalize_environment(value: Any) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("effect:bash:script environment must be a mapping")
        env: dict[str, str] = {}
        for key, raw_value in value.items():
            normalized_key = str(key).strip()
            if not normalized_key:
                continue
            env[normalized_key] = "" if raw_value is None else str(raw_value)
        return env

    @staticmethod
    def _as_float_or_none(value: Any) -> float | None:
        if value is None or value == "":
            return None
        return float(value)

    @staticmethod
    def _parse_stdout_json_object(stdout: str) -> dict[str, Any] | None:
        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        for candidate in reversed(lines):
            if not candidate.startswith("{"):
                continue
            try:
                payload = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
        return None
