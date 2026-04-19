from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from agent_sandbox.workflow_binding import resolve_workflow_bindings


class VmCopyFilesNodeExecutor:
    def __init__(self, fallback_executor: Any) -> None:
        self._fallback_executor = fallback_executor

    def prepare_resolved_config(self, **kwargs: Any) -> dict[str, Any]:
        resolved_config = dict(kwargs.get("resolved_config") or {})
        upstream_payload = kwargs["upstream_payload"]
        declared_upstream_output_names = kwargs["declared_upstream_output_names"]
        system_value_resolver = kwargs["system_value_resolver"]

        next_config = deepcopy(resolved_config)
        raw_files = next_config.get("files")
        if not isinstance(raw_files, list):
            return next_config
        for index, raw_entry in enumerate(raw_files):
            if not isinstance(raw_entry, dict):
                continue
            source_format = str(raw_entry.get("source_format") or "").strip().lower()
            if source_format not in {"", "file", "template"}:
                raise ValueError(f"effect:vm:copy-files files[{index}] source_format must be one of: file, template")
            if source_format != "template":
                continue
            source_path = str(raw_entry.get("source_path") or "").strip()
            if not source_path:
                raise ValueError(f"effect:vm:copy-files files[{index}] source_path is required for template sources")
            template_path = Path(source_path).expanduser().resolve()
            if not template_path.exists():
                raise ValueError(f"effect:vm:copy-files template source does not exist: {template_path}")
            if template_path.is_dir():
                raise ValueError(f"effect:vm:copy-files template source must be a file: {template_path}")
            template_text = template_path.read_text(encoding="utf-8")
            rendered = resolve_workflow_bindings(
                template_text,
                upstream_payload=upstream_payload,
                declared_upstream_output_names=declared_upstream_output_names,
                system_value_resolver=system_value_resolver,
                strict_validation=True,
            ).resolved_config
            if not isinstance(rendered, str):
                raise ValueError(f"effect:vm:copy-files template source must resolve to text: {template_path}")
            raw_entry["content"] = rendered
            raw_entry.pop("source_path", None)
        return next_config

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        return self._fallback_executor.execute(**kwargs)
