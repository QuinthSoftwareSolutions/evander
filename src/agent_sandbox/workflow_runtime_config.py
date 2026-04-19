from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from .config import GatewayConfig, TriggerOrchestratorWorkerGlobalConfig
from .workflow_compiler import WorkflowCompilationPolicies, compile_trigger_orchestrator_config

logger = logging.getLogger(__name__)


def _worker_global_config_path(project_dir: Path) -> Path:
    return project_dir / "config" / "trigger-orchestrator" / "config.json"


def _base_worker_global_payload(base_config: GatewayConfig | None) -> dict[str, object]:
    return TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": (base_config.runtime_dir if base_config is not None else Path("runtime")),
        }
    ).model_dump(mode="json")


def load_trigger_orchestrator_worker_global_config(
    project_dir: Path,
    *,
    base_config: GatewayConfig | None = None,
) -> TriggerOrchestratorWorkerGlobalConfig:
    base_payload = _base_worker_global_payload(base_config)
    path = _worker_global_config_path(project_dir)
    if not path.exists():
        return compile_trigger_orchestrator_config(
            TriggerOrchestratorWorkerGlobalConfig.model_validate(base_payload),
            policies=WorkflowCompilationPolicies(augment_await_agent_completion=True),
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"worker config file {path} must contain a JSON object")
    return compile_trigger_orchestrator_config(
        TriggerOrchestratorWorkerGlobalConfig.model_validate({**base_payload, **raw}),
        policies=WorkflowCompilationPolicies(augment_await_agent_completion=True),
    )


class TriggerOrchestratorWorkerGlobalConfigReloader:
    def __init__(
        self,
        *,
        project_dir: Path,
        initial_config: TriggerOrchestratorWorkerGlobalConfig,
        base_config: GatewayConfig | None = None,
    ) -> None:
        self._path = _worker_global_config_path(project_dir)
        self._base_payload = _base_worker_global_payload(base_config)
        self._current_config = initial_config
        self._last_fingerprint = self._compute_fingerprint()

    def get_latest(self) -> tuple[TriggerOrchestratorWorkerGlobalConfig, bool]:
        return self._reload(force=False)

    def reload_now(self) -> tuple[TriggerOrchestratorWorkerGlobalConfig, bool]:
        return self._reload(force=True)

    def _reload(self, *, force: bool) -> tuple[TriggerOrchestratorWorkerGlobalConfig, bool]:
        fingerprint = self._compute_fingerprint()
        if not force and fingerprint == self._last_fingerprint:
            return self._current_config, False

        try:
            next_config = self._load_from_path()
        except Exception:
            self._last_fingerprint = fingerprint
            logger.error(
                "Failed to reload trigger orchestrator worker config path=%s; keeping last-known-good",
                self._path,
                exc_info=True,
            )
            return self._current_config, False

        changed = next_config != self._current_config
        self._current_config = next_config
        self._last_fingerprint = fingerprint
        return self._current_config, changed

    def _compute_fingerprint(self) -> tuple[int, int, int, str] | None:
        try:
            stat = self._path.stat()
        except FileNotFoundError:
            return None
        try:
            payload = self._path.read_bytes()
        except OSError:
            return None
        digest = hashlib.sha256(payload).hexdigest()
        return stat.st_mtime_ns, stat.st_ino, stat.st_size, digest

    def _load_from_path(self) -> TriggerOrchestratorWorkerGlobalConfig:
        if not self._path.exists():
            return compile_trigger_orchestrator_config(
                TriggerOrchestratorWorkerGlobalConfig.model_validate(self._base_payload),
                policies=WorkflowCompilationPolicies(augment_await_agent_completion=True),
            )
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"worker config file {self._path} must contain a JSON object")
        return compile_trigger_orchestrator_config(
            TriggerOrchestratorWorkerGlobalConfig.model_validate({**self._base_payload, **raw}),
            policies=WorkflowCompilationPolicies(augment_await_agent_completion=True),
        )


class FixedTriggerOrchestratorWorkerGlobalConfigReloader:
    def __init__(self, config: TriggerOrchestratorWorkerGlobalConfig) -> None:
        self._config = config

    def get_latest(self) -> tuple[TriggerOrchestratorWorkerGlobalConfig, bool]:
        return self._config, False

    def reload_now(self) -> tuple[TriggerOrchestratorWorkerGlobalConfig, bool]:
        return self._config, False
